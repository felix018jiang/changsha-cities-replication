"""Re-route two complete ASPECT circles and rank each frozen single addition.

Run from the project with work/round3/.venv/bin/python work/round17/population/full_circle.py.
"""
from pathlib import Path
import hashlib
import json
import sys
from tempfile import NamedTemporaryFile

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse.csgraph import dijkstra

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "work"
POP = WORK / "round13/population"
SPATIAL = WORK / "round4/spatial"
R15 = ROOT / "outputs/长沙养老研究_第十五轮/tables"
OUT = ROOT / "outputs/长沙养老研究_第十七轮/tables"
sys.path.insert(0, str(POP))
sys.path.insert(0, str(WORK / "round4/code"))
from build_weights import AREA_CRS, read_density
from macro_network import load_round3_graph_functions, read_network

CASES = ["FR-XH-1", "WC-GTL-21"]
SCENARIOS = ["strict", "wide"]
SUPPORTS = ["residential_250m", "native_residential_weight", "native_whole_circle"]
BUDGET = 720.0
TOL = 1e-8  # R14/R15 population tie tolerance
KEY = ["case_id", "origin_id"]
DIMS = ["case_id", "scenario", "support"]
BATCH_FILES = {
    "original_eight": ("round14/activity_review.csv", "round13/rules/extra_source_venues.csv",
                       "round13/rules/extra_reach.csv"),
    "additional_eight": ("round15/review/activity_review.csv", "round15/inputs/extra_source_venues.csv",
                         "round15/inputs/site_reach.csv"),
}
TWO_CIRCLE_SOURCES = [(CASES[0], *BATCH_FILES["original_eight"]),
                      (CASES[1], *BATCH_FILES["additional_eight"])]


def native_origins(case_ids=CASES):
    """Same native anchor for mask and entire-circle weights, including boundary cells."""
    x, y, values, valid = read_density()
    zone = gpd.read_file(SPATIAL / "exclusive_circle_zones.gpkg", layer="exclusive_circles")
    zone = zone[zone.circle_id.isin(case_ids)][["circle_id", "geometry"]]
    residential = gpd.read_file(SPATIAL / "macro_residential.gpkg", layer="residential_clip_250m")
    # Intersect in the original metric CRS, then densify and project each fragment.
    # These are the geometries used for R13's reference residential population.
    parts = gpd.overlay(residential[["geometry"]], zone, keep_geom_type=True)
    parts.geometry = parts.geometry.segmentize(50).to_crs(AREA_CRS).make_valid()
    zone.geometry = zone.geometry.segmentize(50).to_crs(AREA_CRS).make_valid()
    reference = pd.read_csv(POP / "circle_population_support.csv").set_index("case_id")
    rows = []
    for reg in zone.itertuples():
        mask = parts.loc[parts.circle_id.eq(reg.circle_id)].geometry.union_all()
        left, bottom, right, top = reg.geometry.bounds
        assert x[0] <= left < right <= x[-1] and y[0] <= bottom < top <= y[-1]
        nodata_area = 0.
        for row in range(y.searchsorted(bottom, side="right")-1, y.searchsorted(top)):
            for col in range(x.searchsorted(left, side="right")-1, x.searchsorted(right)):
                cell = shapely.box(x[col], y[row], x[col+1], y[row+1])
                part = cell.intersection(reg.geometry)
                if part.area <= 0:
                    continue
                if not valid[row, col]:
                    nodata_area += part.area
                    continue
                mass = values[row, col, :2].sum()
                if mass <= 0:
                    continue
                res_area = cell.intersection(mask).area
                assert res_area <= part.area + 0.01, (reg.circle_id, row, col, res_area, part.area)
                res_area = min(res_area, part.area)
                geometries = [p for p in shapely.get_parts(part) if p.area > 0]
                anchor = max(geometries, key=lambda p: p.area).representative_point()
                top_row = valid.shape[0] - 1 - row
                rows.append(dict(case_id=reg.circle_id, origin_id=f"ASPECT_{reg.circle_id}_{top_row}_{col}",
                                 geometry=anchor, crop_row=top_row, crop_col=col, cell_area_m2=cell.area,
                                 circle_area_m2=part.area, residential_area_m2=res_area,
                                 cell_population=mass, circle_population=mass*part.area/cell.area,
                                 residential_population=mass*res_area/cell.area))
        np.testing.assert_allclose(nodata_area, reference.at[reg.circle_id, "nodata_area_m2_domain"],
                                   rtol=0, atol=.001)
    native = gpd.GeoDataFrame(rows, crs=AREA_CRS).to_crs(32649).sort_values(KEY)
    for case, subset in native.groupby("case_id"):
        np.testing.assert_allclose(subset.circle_population.sum(), reference.at[case, "older_population_domain"],
                                   rtol=0, atol=1e-5)
        np.testing.assert_allclose(subset.residential_population.sum(),
                                   reference.at[case, "older_population_residential"], rtol=0, atol=.001)
    assert native.origin_id.is_unique
    return native


def reference_inputs(case_ids=CASES):
    origins = pd.read_csv(R15 / "decision_origins.csv").query("k==1 and case_id in @case_ids")
    actions = pd.read_csv(R15 / "decision_first_actions.csv", dtype={"benefit_pattern": str})
    actions = actions.query("k==1 and case_id in @case_ids and scenario in @SCENARIOS").copy()
    assert not actions.duplicated(["case_id", "scenario", "plan_id"]).any()
    base = origins[KEY+["older_population"]].drop_duplicates()
    assert not base.duplicated(KEY).any()
    pts = gpd.read_file(SPATIAL / "macro_residential.gpkg", layer="residential_origins_250m")
    pts = pts[["origin_id", "geometry"]].merge(base, on="origin_id", validate="one_to_one")
    assert len(pts) == len(base)
    return pts, origins, actions


def service_and_site_inventory(actions, sources=TWO_CIRCLE_SOURCES):
    columns = ["record_id", "function", "geometry"]
    archive = WORK / "round7/analysis"
    eligible = json.loads((archive / "evidence_sets.json").read_text())["uniform_updated"]
    global_ids = {f: set(eligible[f]) for f in "HSG"}
    uniform = gpd.read_file(archive / "routing_targets.gpkg")
    uniform = uniform[uniform.record_id.isin(set.union(*global_ids.values()))][columns].copy()
    assert set(uniform.record_id) == set.union(*global_ids.values())
    nationwide = pd.read_csv(ROOT / "outputs/长沙养老研究_第十三轮/tables/national_routing_scope.csv")
    conf = pd.read_csv(WORK / "round15/inputs/strict_conflicting_meal_pins.csv")
    withheld = set(conf.loc[conf.newly_removed_from_strict_route, "evidence_id"])
    meals = nationwide[nationwide.inside_routing_support & nationwide.included_wide].copy()
    national_ids = {"strict": set(meals.loc[meals.included_primary & ~meals.evidence_id.isin(withheld), "routing_id"]),
                    "wide": set(meals.routing_id)}
    assert [len(national_ids[s]) for s in SCENARIOS] == [566, 611]
    meals = gpd.GeoDataFrame({"record_id": meals.routing_id, "function": "M"},
                             geometry=gpd.points_from_xy(meals.x_utm49n, meals.y_utm49n), crs=32649)
    local = {}
    pins = []
    for case, ledger_path, sites_path, _ in sources:
        ledger = pd.read_csv(WORK/ledger_path).query("case_id==@case")
        coords = pd.read_csv(WORK/sites_path).query("case_id==@case")
        coords = coords[["site_id","lon_wgs84","lat_wgs84"]].drop_duplicates()
        rows = ledger.merge(coords, on="site_id", validate="one_to_one")
        assert len(rows)==len(ledger)
        for f, use in [("M", rows.verified_extra_meal.eq(1)), ("S", rows.classification.eq("verified_public_activity"))]:
            assert rows.loc[use, "position_unresolved"].eq(0).all()
            local[case,f] = set(rows.loc[use, "site_id"])
        allowed = set(actions.loc[actions.case_id.eq(case), "site_ids"])
        required = allowed | local[case,"S"] | local[case,"M"]
        assert required <= set(rows.site_id)
        use = rows[rows.site_id.isin(required)]
        # A local station point can supply several functions; its graph node is shared.
        point = gpd.GeoDataFrame({"record_id": use.site_id, "function": "site"},
                                 geometry=gpd.points_from_xy(use.lon_wgs84, use.lat_wgs84), crs=4326)
        pins.append(point.to_crs(32649))
    data = gpd.GeoDataFrame(pd.concat([uniform,meals,*pins], ignore_index=True), crs=32649)
    assert data.record_id.is_unique
    return data, global_ids, national_ids, local


def route(pts, actions, sources=TWO_CIRCLE_SOURCES):
    targets, global_ids, national_ids, local = service_and_site_inventory(actions, sources)
    edges, xy, _ = read_network()
    lib = load_round3_graph_functions()
    coords, arcs, nodes, parks, snaps = lib.augment(edges, xy, pts, targets)
    reverse = lib.make_csr(arcs, len(coords)).T.tocsr()
    oi = np.array([nodes[oid] for oid in pts.origin_id])
    def distances(ids, f):
        dest = sorted({n for rid in ids for n in (parks[rid] if f=="G" else [nodes[rid]])})
        if not dest:
            return np.full(len(oi), np.inf)  # This circle has no confirmed local override.
        # Untruncated: inf means no directed route; finite distances can exceed 720 m.
        return dijkstra(reverse, directed=True, indices=dest, min_only=True)[oi]
    dist_global = {f: distances(ids, f) for f,ids in global_ids.items()}
    national = {s: distances(ids, "M") for s,ids in national_ids.items()}
    baseline, reach = [], []
    for case, *_ in sources:
        where = pts.case_id.eq(case)
        local_m = distances(local[case, "M"], "M")
        local_s = distances(local[case, "S"], "S")
        for s in SCENARIOS:
            data = pts.loc[where, KEY].copy()
            data["scenario"] = s
            final = {"H":dist_global["H"], "G":dist_global["G"], "S":np.minimum(dist_global["S"], local_s),
                     "M":np.minimum(national[s], local_m)}
            for f in "HMSG":
                data["distance_"+f+"_m"] = final[f][where]
                data["covered_"+f] = data["distance_"+f+"_m"].le(BUDGET)
            data["initial_deficit"] = 4 - data[["covered_"+f for f in "HMSG"]].sum(axis=1)
            baseline.append(data)
        for sid in sorted(actions.loc[actions.case_id.eq(case), "site_ids"].unique()):
            r = pts.loc[where, KEY].copy()
            r["site_id"] = sid
            r["distance_site_m"] = distances([sid], "site")[where]
            r["within_threshold"] = r.distance_site_m.le(BUDGET)
            reach.append(r)
    origin_snaps = snaps[snaps.role.eq("origin")][["object_id","connector_m"]].rename(columns={"object_id":"origin_id"})
    assert len(origin_snaps)==len(pts)
    return pd.concat(baseline, ignore_index=True), pd.concat(reach, ignore_index=True), origin_snaps


def validate_reference(base, reach, origins, actions, sources=TWO_CIRCLE_SOURCES):
    checked = origins.merge(base, on=KEY+["scenario"], validate="one_to_one", suffixes=("_r15","_new"))
    assert len(checked)==len(origins)
    for f in "HMSG":
        assert checked["covered_"+f+"_r15"].eq(checked["covered_"+f+"_new"]).all(), f
    reference_raw = []
    for case, _, _, path in sources:
        rr = pd.read_csv(WORK / path).query("case_id==@case")
        ids = set(actions.loc[actions.case_id.eq(case), "site_ids"])
        rr = rr[rr.site_id.isin(ids)][KEY+["site_id","within_threshold"]].drop_duplicates()
        reference_raw.append(rr)
    rr = pd.concat(reference_raw)
    ck = rr.merge(reach, on=KEY+["site_id"], validate="one_to_one", suffixes=("_old","_new"))
    ref_ids = set(origins.origin_id)
    assert len(ck)==len(rr)==int(reach.origin_id.isin(ref_ids).sum())
    assert ck.within_threshold_old.eq(ck.within_threshold_new).all()
    return {"reference_HMSG_flags_checked":len(checked)*4, "reference_origin_site_pairs_checked":len(ck)}


def evaluate(native, ref, base, reach, archived, origins):
    lookup = native[KEY+["circle_population","residential_population"]]
    support_rows = []
    for name, src, mass in [(SUPPORTS[0],ref,"older_population"),(SUPPORTS[1],lookup,"residential_population"),
                             (SUPPORTS[2],lookup,"circle_population")]:
        weights = src[KEY+[mass]].rename(columns={mass:"older_population"})
        if name != SUPPORTS[0]:
            weights = weights[weights.older_population.gt(0)]
        flags = base.merge(weights, on=KEY, validate="many_to_one")
        flags["support"] = name
        support_rows.append(flags)
    status = pd.concat(support_rows, ignore_index=True)
    plans = []
    benefits = []
    for (cid, scenario, support), frame in status.groupby(DIMS, sort=False):
        actions = archived[(archived.case_id==cid) & (archived.scenario==scenario)]
        join = frame.merge(reach, on=KEY, validate="one_to_many")
        for action in actions.itertuples():
            f = action.function_mix
            part = join.loc[join.site_id.eq(action.site_ids)].copy()
            assert len(part)==len(frame)
            gain = part.within_threshold & ~part["covered_"+f]
            part["helped"] = gain
            part["plan_id"] = action.plan_id
            benefits.append(part.loc[gain, DIMS+["origin_id","plan_id","initial_deficit","older_population"]])
            helped = float(part.older_population[gain].sum())
            severe = float(part.older_population[gain & part.initial_deficit.ge(3)].sum())
            if support==SUPPORTS[0]:
                np.testing.assert_allclose([helped, severe], [action.helped, action.helped_D34], rtol=0, atol=TOL)
                order = origins[(origins.case_id==cid)&(origins.scenario==scenario)].sort_values("bit_index")
                rec = order[KEY].merge(part[KEY+["helped"]], on=KEY, validate="one_to_one")
                packed = np.packbits(rec.helped.to_numpy(bool), bitorder="little").tobytes().hex()
                assert packed == action.benefit_pattern
            plans.append(dict(case_id=cid, scenario=scenario, support=support, site_id=action.site_ids,
                              site_name=action.site_names, plan_id=action.plan_id, function=f, helped=helped,
                              helped_D34=severe, n_helped_origins=int(gain.sum()),
                              n_helped_D34_origins=int((gain & part.initial_deficit.ge(3)).sum())))
    scores = pd.DataFrame(plans)
    for score, chosen in [("helped","selected_overall_first"),("helped_D34","selected_severe_first")]:
        best = scores.groupby(DIMS)[score].transform("max")
        scores[chosen] = np.isclose(scores[score], best, rtol=0, atol=TOL)
        compare = scores.query("support==@SUPPORTS[0]").merge(archived, on=["scenario","case_id","plan_id"],
                                                              suffixes=("_r17","_r15"), validate="one_to_one")
        assert compare[chosen+"_r17"].eq(compare[chosen+"_r15"]).all()
    membership = pd.concat(benefits, ignore_index=True)
    assert not membership.duplicated(DIMS+["origin_id","plan_id"]).any()
    return status, scores, membership


def main(case_ids=CASES, output=OUT, checks_path=Path(__file__).parent/"checks.json"):
    output.mkdir(parents=True, exist_ok=True)
    native = native_origins(case_ids)
    ref, frozen, actions = reference_inputs(case_ids)
    batches = frozen[["case_id", "batch"]].drop_duplicates().set_index("case_id").batch
    assert batches.index.is_unique and set(batches.index) == set(case_ids)
    sources = [(case, *BATCH_FILES[batches[case]]) for case in case_ids]
    pts = gpd.GeoDataFrame(pd.concat([ref[KEY+["geometry"]],native[KEY+["geometry"]]],ignore_index=True),crs=32649)
    assert pts.origin_id.is_unique
    base, reach, snaps = route(pts, actions, sources)
    qa = validate_reference(base, reach, frozen, actions, sources)
    status, scores, membership = evaluate(native, ref, base, reach, actions, frozen)
    denominators = status.groupby(DIMS, as_index=False).agg(n_origins=("origin_id","size"),older_population=("older_population","sum"))
    severe = status.loc[status.initial_deficit.ge(3)].groupby(DIMS).older_population.sum().rename("older_population_D34")
    summary = denominators.merge(severe,on=DIMS,how="left",validate="1:1").fillna({"older_population_D34":0})
    selected = scores.loc[scores.selected_overall_first | scores.selected_severe_first]
    summary = summary.merge(selected,on=DIMS,how="left",validate="one_to_many")
    pts["x_utm49n_m"] = pts.geometry.x
    pts["y_utm49n_m"] = pts.geometry.y
    xy = pts.drop(columns="geometry").merge(snaps,on="origin_id",validate="1:1")
    status = status.merge(xy,on=KEY,validate="many_to_one")
    counts = {"origin_support":status, "action_scores":scores, "choices":summary, "newly_reached_origins":membership,
              "site_distances":reach, "native_weights":native.drop(columns="geometry").merge(xy,on=KEY,validate="1:1")}
    for suffix, df in counts.items():
        dest = output/f"population_{suffix}.csv"
        with NamedTemporaryFile(dir=output, suffix=".csv", delete=False) as handle:
            temp = Path(handle.name)
        try:
            df.to_csv(temp, index=False, float_format="%.12g")
            temp.replace(dest)
        finally:
            temp.unlink(missing_ok=True)
    qa.update({"case_ids":list(case_ids), "scenarios":SCENARIOS, "walking_budget_m":BUDGET, "estimated_age":"60+",
               "national_meal_points":{"strict":566,"wide":611}, "reference_action_benefit_vectors_checked":len(actions),
               "reference_origin_count":len(ref),"reference_action_scores_per_plan_checked":2,
               "ranked_criteria":["helped","helped_D34"], "tie_tolerance_persons":TOL,
               "census_grid_units":"2020 ASPECT native pixel counts; overlap area fraction in EPSG:6933",
               "pixel_weight_sum_tolerances":{"full_persons":1e-5,"residential_persons":.001},
               "circles_with_population_nodata":int(pd.read_csv(POP/"circle_population_support.csv").set_index("case_id").loc[case_ids, "nodata_area_m2_domain"].gt(0).sum()),
               "no_path_definition":"Untruncated directed distances; inf = unreachable. Beyond 720 m is kept as finite if reachable.",
               "main_score_check":"Every reproduced 250m action's beneficiary bit vector, total benefit, severe benefit and two selection flags match R15.",
               "population_source":"https://doi.org/10.6084/m9.figshare.27323106.v1"})
    qa["unreachable_native_origins_by_service"] = {}
    for (case, scenario), frame in status[status.support.eq("native_whole_circle")].groupby(["case_id","scenario"]):
        qa["unreachable_native_origins_by_service"][f"{case}-{scenario}"] = {
            f:int((~np.isfinite(frame["distance_"+f+"_m"])).sum()) for f in "HMSG"}
    qa["input_sha256"] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                           [POP/"source_manifest.json", SPATIAL/"exclusive_circle_zones.gpkg",
                            WORK/"round7/analysis/evidence_sets.json", WORK/"round4/analysis/network_meta.json",
                            WORK/"round14/activity_review.csv", WORK/"round15/review/activity_review.csv",
                            WORK/"round15/inputs/strict_conflicting_meal_pins.csv",
                            R15/"decision_first_actions.csv", R15/"decision_origins.csv"]}
    qa_path = checks_path
    with NamedTemporaryFile(dir=qa_path.parent, suffix=".json", mode="w", delete=False) as handle:
        json.dump(qa, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(qa_path)
    return summary


if __name__ == "__main__":
    print(main()[["case_id","scenario","support","site_name","helped","helped_D34","selected_overall_first",
                  "selected_severe_first"]].to_string(index=False))
