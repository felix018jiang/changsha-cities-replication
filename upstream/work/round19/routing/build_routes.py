"""Route actual venues on the frozen walking network and build incremental coverage sets.

Run with work/round3/.venv/bin/python work/round19/routing/build_routes.py.
The optional --station-only flag writes a separate baseline audit fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
R18 = ROOT / "outputs/长沙养老研究_第十八轮/tables"
OUT = ROOT / "outputs/长沙养老研究_第十九轮/tables"
OFFSITE = ROOT / "work/round19/venues/offsite_candidates.csv"
REGISTRIES = ("strict", "wide")
CUTOFF = 720.0
KEY = ["case_id", "origin_id"]
ACTION = ["case_id", "scenario", "function", "site_id"]
STATION_SOURCE = "https://ylfw.cszhmz.cn/psp/pc/yldt_1692694093000/index.html"
sys.path.insert(0, str(ROOT / "work/round17/population"))
from full_circle import native_origins, load_round3_graph_functions, read_network


def read_csv(path):
    return pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)


def atomic_csv(table, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".csv", delete=False) as handle:
        stage = Path(handle.name)
    try:
        table.to_csv(stage, index=False, float_format="%.12g")
        stage.chmod(0o644)
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def frozen_stations():
    scores = read_csv(R18 / "population_action_scores.csv").query("support=='native_whole_circle'").copy()
    assert not scores.duplicated(ACTION).any()
    records = pd.concat([read_csv(ROOT / "work/round13/rules/extra_source_venues.csv"),
                         read_csv(ROOT / "work/round15/inputs/extra_source_venues.csv")], ignore_index=True)
    sites = records.groupby(["case_id", "site_id"], as_index=False).agg(
        site_address=("address", lambda col: " | ".join(sorted(set(col)))),
        lon_wgs84=("lon_wgs84", "first"), lat_wgs84=("lat_wgs84", "first"))
    assert not (records.groupby("site_id")[["lon_wgs84","lat_wgs84"]].nunique() > 1).any().any()
    candidates = scores[ACTION + ["site_name"]].merge(sites, on=["case_id", "site_id"], validate="many_to_one")
    assert len(candidates) == len(scores)
    candidates = candidates.assign(pool_type="inside", source_url=STATION_SOURCE, existing_service=False,
                                   evidence_tier="frozen_station_eligibility", venue_type="eldercare_station")
    return candidates, scores


def prepare_offsite(path):
    data = read_csv(path).rename(columns={"address": "site_address"})
    required = set(ACTION) - {"scenario"} | {"site_name", "site_address", "lon_wgs84", "lat_wgs84", "source_url"}
    if not required <= set(data):
        raise ValueError(f"Missing columns from {path}: {sorted(required-set(data))}")
    if "scenario" not in data:
        data["scenario"] = "both"
    if "existing_service" not in data:
        data["existing_service"] = False
    data["existing_service"] = data.existing_service.astype(str).str.lower()
    valid_status = {"0", "1", "false", "true", "no", "yes", ""}
    assert set(data.existing_service) <= valid_status
    data["existing_service"] = data.existing_service.isin(["1", "true", "yes"])
    if not set(data.scenario) <= {"all", "both", "strict", "wide", ""}:
        raise ValueError("Unknown registry scenario")
    data["pool_type"] = "offsite"
    data = pd.concat([data.loc[data.scenario.isin(["both", "all", "", scenario])].assign(scenario=scenario)
                      for scenario in REGISTRIES], ignore_index=True)
    assert set(data.function) <= {"M", "S"}
    assert not data.duplicated(ACTION).any()
    assert data.site_id.str.len().gt(0).all() and data.site_name.str.len().gt(0).all()
    assert data.groupby(["site_id", "scenario", "function"]).existing_service.nunique().le(1).all()
    assert data.lon_wgs84.between(112, 115).all() and data.lat_wgs84.between(27, 30).all()
    return data


def build_graph(origins, locations):
    # Recreate the original cell anchors at full precision. Reprojecting the rounded table
    # alone can move a point to the opposite half-edge in exact equidistance cases.
    points = native_origins(sorted(origins.case_id.unique()))
    probe = points[KEY].assign(x=points.geometry.x, y=points.geometry.y).merge(
        origins.query("scenario=='strict'")[KEY + ["x_utm49n_m", "y_utm49n_m"]], on=KEY, validate="one_to_one")
    assert len(probe) == len(points) == 5959
    np.testing.assert_allclose(probe.x, probe.x_utm49n_m, rtol=0, atol=.00001)
    np.testing.assert_allclose(probe.y, probe.y_utm49n_m, rtol=0, atol=.00001)
    target = gpd.GeoDataFrame({"record_id": locations.site_id, "function": "site"},
                             geometry=gpd.points_from_xy(locations.lon_wgs84, locations.lat_wgs84),
                             crs=4326).to_crs(32649)
    edges, xy, _ = read_network()
    lib = load_round3_graph_functions()
    coords, arcs, nodes, _, snaps = lib.augment(edges, xy, points, target)
    graph = lib.make_csr(arcs, len(coords)).T.tocsr()
    origin_nodes = np.asarray([nodes[oid] for oid in points.origin_id])
    records = []
    for site in target.record_id:
        distances = dijkstra(graph, directed=True, indices=nodes[site], limit=CUTOFF)[origin_nodes]
        hit = np.isfinite(distances)
        if hit.any():
            selected = points.loc[hit, KEY].copy()
            selected["site_id"] = site
            selected["distance_m"] = distances[hit]
            records.append(selected)
    hits = pd.concat(records, ignore_index=True)
    assert not hits.duplicated(KEY + ["site_id"]).any()
    return points, graph, nodes, origin_nodes, hits, snaps


def verify_forward_paths(points, reverse_graph, nodes, locations, hits):
    """Check three origins per circle against all venue nodes in the forward graph."""
    forward_graph = reverse_graph.T.tocsr()
    site_ids = locations.site_id.tolist()
    site_nodes = np.array([nodes[site] for site in site_ids])
    matched_origins = set(hits.origin_id)
    chosen = set()
    for _, group in points.groupby("case_id"):
        origin_ids = sorted(group.origin_id)
        reached = [o for o in origin_ids if o in matched_origins]
        missed = [o for o in origin_ids if o not in matched_origins]
        sample = list(dict.fromkeys(reached[:1] + missed[:1] + [origin_ids[len(origin_ids)//2]] + origin_ids[:3]))
        chosen.update(sample[:3])
    hits_by_origin = {oid: group.set_index("site_id").distance_m for oid,group
                      in hits.loc[hits.origin_id.isin(chosen)].groupby("origin_id")}
    checked_reachable = 0
    for oid in sorted(chosen):
        forward = dijkstra(forward_graph, directed=True, indices=nodes[oid], limit=CUTOFF)[site_nodes]
        saved = hits_by_origin.get(oid, pd.Series(dtype=float)).reindex(site_ids).fillna(np.inf).to_numpy()
        np.testing.assert_allclose(forward, saved, rtol=0, atol=1e-7)
        checked_reachable += int(np.isfinite(forward).sum())
    return {"forward_check_origins":len(chosen),"forward_venue_origin_pairs":len(chosen)*len(site_ids),
            "forward_check_reachable_pairs":checked_reachable}


def update_baseline(origins, corrections, points, graph, nodes, origin_nodes):
    revised = origins.copy()
    for col in ("M","S"):
        revised[f"covered_{col}_r18"] = revised[f"covered_{col}"]
    revised["initial_deficit_r18"] = revised.initial_deficit
    for (sc,f), group in corrections.groupby(["scenario", "function"]):
        ids = sorted({nodes[site] for site in group.site_id})
        best = dijkstra(graph, directed=True, indices=ids, min_only=True)[origin_nodes]
        new_dist = pd.Series(best, index=points.origin_id)
        select = revised.scenario.eq(sc)
        dcol = f"distance_{f}_m"
        revised.loc[select, dcol] = np.minimum(revised.loc[select, dcol],
                                              revised.loc[select,"origin_id"].map(new_dist).to_numpy())
        revised.loc[select, f"covered_{f}"] = revised.loc[select, dcol].le(CUTOFF)
    revised["initial_deficit"] = 4 - revised[[f"covered_{s}" for s in "HMSG"]].sum(axis=1)
    return revised


def coverage_links(origins, candidates, hits):
    joined = candidates[ACTION].merge(hits, on=["case_id", "site_id"], validate="many_to_many")
    joined = joined.merge(origins[KEY+["scenario","covered_M","covered_S","older_population","initial_deficit"]],
                          on=KEY+["scenario"], validate="many_to_one")
    needed = ((joined.function.eq("M") & ~joined.covered_M) |
              (joined.function.eq("S") & ~joined.covered_S))
    gained = joined.loc[needed, ACTION+["origin_id","older_population","initial_deficit","distance_m"]].copy()
    assert not gained.duplicated(ACTION+["origin_id"]).any()
    return gained


def verify_old(origins, stations, scores, hits):
    archive = read_csv(R18 / "population_site_distances.csv")
    archive = archive.loc[archive.origin_id.isin(origins.origin_id) & archive.within_threshold]
    rerouted = hits.loc[hits.site_id.isin(stations.site_id)]
    merge = archive.merge(rerouted, on=KEY+["site_id"], validate="one_to_one", how="outer", indicator=True)
    assert merge._merge.eq("both").all(), merge.loc[merge._merge.ne("both")].head()
    delta = abs(merge.distance_site_m - merge.distance_m)
    assert delta.max() < 1e-5, delta.max()
    baseline_links = coverage_links(origins, stations, hits)
    reference = read_csv(R18 / "population_newly_reached_origins.csv").query("support=='native_whole_circle'")
    reference = reference.merge(scores[ACTION+["plan_id"]], on=["case_id", "scenario","plan_id"],
                                validate="many_to_one")
    check = reference.merge(baseline_links, on=ACTION+["origin_id"], how="outer", indicator=True, validate="one_to_one")
    assert check._merge.eq("both").all()
    np.testing.assert_allclose(check.older_population_x, check.older_population_y, atol=1e-7,rtol=0)
    grouped = baseline_links.groupby(ACTION).older_population.sum().rename("rerouted_gain")
    ranked = scores.merge(grouped,on=ACTION,how="left",validate="one_to_one").fillna({"rerouted_gain":0})
    np.testing.assert_allclose(ranked.helped, ranked.rerouted_gain, atol=1e-6, rtol=0)
    union = baseline_links.drop_duplicates(["case_id","scenario","function","origin_id"])
    grouped = union.groupby(["case_id","scenario","function"]).older_population.sum().rename("rerouted_upper")
    upper = read_csv(R18 / "circle_reach_upper_bounds.csv").query("support=='native_whole_circle'").rename(columns={"registry":"scenario"})
    upper = upper.merge(grouped,on=["case_id","scenario","function"],how="left",validate="one_to_one").fillna({"rerouted_upper":0})
    assert len(upper)==64
    np.testing.assert_allclose(upper.rerouted_upper, upper.attainable_population, rtol=0, atol=1e-6)
    return {"station_reach_pairs":len(merge), "max_station_distance_disagreement_m":float(delta.max()),
            "reproduced_station_gain_links":len(check), "reproduced_station_action_scores":len(ranked),
            "reproduced_station_circle_registry_function_limits":len(upper)}


def circle_boundary_audit(eligible, hits, links):
    zones = gpd.read_file(ROOT/"work/round4/spatial/exclusive_circle_zones.gpkg", layer="exclusive_circles")
    zones = zones.to_crs(32649).set_index("circle_id")
    pairs = eligible[["case_id","site_id","pool_type","lon_wgs84","lat_wgs84","connector_m"]].drop_duplicates()
    geometry = gpd.GeoSeries(gpd.points_from_xy(pairs.lon_wgs84, pairs.lat_wgs84), crs=4326).to_crs(32649)
    pairs["site_outside_circle"] = [not zones.loc[c].geometry.covers(p) for c,p in zip(pairs.case_id,geometry)]
    by = ["case_id","site_id"]
    reach_counts = hits.groupby(by).origin_id.nunique().rename("reachable_origins")
    strict_counts = links.query("scenario=='strict'").groupby(by).origin_id.nunique().rename("strict_gain_origins")
    return pairs.merge(reach_counts,on=by,how="left").merge(strict_counts,on=by,how="left").fillna(
        {"reachable_origins":0,"strict_gain_origins":0}).astype({"reachable_origins":int,"strict_gain_origins":int})


def verify_corrected_flags(origins, final, existing, hits):
    """Rebuild the corrected flags using the 720-m site OD list only."""
    changed = []
    for (scenario, function) in ((s, f) for s in REGISTRIES for f in "MS"):
        field = f"covered_{function}"
        old = origins.loc[origins.scenario.eq(scenario)].set_index("origin_id")
        result = final.loc[final.scenario.eq(scenario)].set_index("origin_id").loc[old.index]
        sites = existing.loc[existing.scenario.eq(scenario) & existing.function.eq(function), "site_id"]
        reached = hits.loc[hits.site_id.isin(sites), "origin_id"]
        expected = old[field] | old.index.isin(reached)
        np.testing.assert_array_equal(expected, result[field])
        newly = old.loc[expected & ~old[field]]
        for case in sorted(old.case_id.unique()):
            local = newly.loc[newly.case_id.eq(case)]
            changed.append({"scenario":scenario, "function":function, "case_id":case,
                            "origins_newly_in_baseline":len(local),
                            "population_newly_in_baseline":local.older_population.sum()})
    return pd.DataFrame(changed)


def main(station_only=False):
    origins_path = R18 / "population_origin_support.csv"
    source = read_csv(origins_path).query("support=='native_whole_circle'").copy()
    assert not source.duplicated(KEY+["scenario"]).any() and len(source)==2*5959
    stations, scores = frozen_stations()
    off = stations.iloc[:0].copy() if station_only else prepare_offsite(OFFSITE)
    if not set(off.case_id) <= set(source.case_id):
        raise ValueError(f"Offsite case outside study: {set(off.case_id)-set(source.case_id)}")
    if set(stations.site_id) & set(off.site_id):
        raise ValueError("Offsite and station IDs overlap")
    combined = pd.concat([stations, off],ignore_index=True)
    entity = combined.drop_duplicates("site_id")
    for field in ("lon_wgs84","lat_wgs84"):
        assert (combined.groupby("site_id")[field].nunique()==1).all(), field
    points, graph, nodes, origin_nodes, hits, snaps = build_graph(source, entity)
    checks = verify_old(source, stations, scores, hits)
    checks.update(verify_forward_paths(points, graph, nodes, entity, hits))
    # Test every OD against all 5,959 origins; the circle annotation on the venue
    # ledger does not restrict the catchment. Retain eligible zero-coverage actions.
    eligible = combined.loc[~combined.existing_service].copy()
    cross = hits[["case_id","site_id"]].drop_duplicates().merge(
        eligible.drop(columns="case_id").drop_duplicates(["site_id","function","scenario"]),
        on="site_id",validate="many_to_many")
    eligible = pd.concat([eligible, cross],ignore_index=True).drop_duplicates(ACTION)
    no_address = eligible.site_address.isna() | eligible.site_address.eq("")
    if no_address.any():
        eligible.loc[no_address, "site_address"] = [
            f"WGS84 {r.lon_wgs84:.6f}, {r.lat_wgs84:.6f}" for r in eligible.loc[no_address].itertuples()
        ]
    facility_snaps = snaps[snaps.role.eq("facility")].rename(columns={"object_id":"site_id"})
    eligible = eligible.merge(facility_snaps[["site_id","connector_m","parent_edge"]],
                              on="site_id",validate="many_to_one")
    existing = combined.loc[combined.existing_service]
    final_origins = update_baseline(source, existing, points, graph, nodes, origin_nodes)
    corrections = verify_corrected_flags(source, final_origins, existing, hits)
    links = coverage_links(final_origins, eligible, hits)
    boundary = circle_boundary_audit(eligible, hits, links)
    output = HERE / "station_only" if station_only else OUT
    for table, name in [(eligible.drop(columns="existing_service"), "routing_candidates"),
                        (final_origins, "routing_origins"),(links, "routing_gains"),
                        (hits, "routing_reachable_od"),
                        (existing, "routing_existing_service_updates"),
                        (corrections, "routing_baseline_delta"),
                        (boundary, "routing_circle_boundary"),
                        (snaps[snaps.role.eq("facility")], "routing_site_connections")]:
        atomic_csv(table, output/(name+".csv"))
    overlap = hits[["case_id","site_id"]].drop_duplicates().groupby("site_id").case_id.nunique()
    outside = boundary.loc[boundary.site_outside_circle]
    checks.update({"unique_native_origins":len(points),"case_count":source.case_id.nunique(),
                   "unique_all_candidates":eligible.site_id.nunique(),"unique_offsite_candidates":eligible.loc[eligible.pool_type.eq("offsite"),"site_id"].nunique(),
                   "unique_existing_service_updates":existing.site_id.nunique(),
                   "circle_site_pairs_outside_reachable":int(outside.reachable_origins.gt(0).sum()),
                   "circle_site_pairs_outside_strict_gain":int(outside.strict_gain_origins.gt(0).sum()),
                   "unique_multicircle_reachable_sites":int(overlap.gt(1).sum()), "walk_distance_m":CUTOFF,
                   "changes_to_r18_by_registry":{s:{f:int((r["covered_"+f] != r["covered_"+f+"_r18"]).sum())
                                                   for f in "MS"} for s,r in final_origins.groupby("scenario")},
                   "sha256_r18_origins":hashlib.sha256(origins_path.read_bytes()).hexdigest(),
                   "sha256_offsite_ledger":None if station_only else hashlib.sha256(OFFSITE.read_bytes()).hexdigest()})
    check_dir = output if station_only else HERE
    (check_dir/"routing_checks.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--station-only",action="store_true")
    main(parser.parse_args().station_only)
