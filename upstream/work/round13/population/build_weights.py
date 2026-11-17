"""Integrate versioned ASPECT densities over exact residential and policy units."""

import hashlib
import json
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from PIL import Image
from pyproj import Transformer
import shapely

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPATIAL = ROOT / "work/round4/spatial"
AREA_CRS = 6933  # Equal-area cylindrical projection: lon/lat cell edges stay straight.
KEY = ["case_id", "origin_id"]
COUNTS = ["age60_64", "age65_plus"]
POPULATION_COLUMNS = COUNTS + ["older_population", "all_age_population"]
DENSITY_SENSITIVITY = [v + "_density_reading" for v in POPULATION_COLUMNS]
MEASURES = POPULATION_COLUMNS + DENSITY_SENSITIVITY + ["zonal_area_m2", "nodata_area_m2"]
AGING_UNIT = "ASPECT pixel mass allocated by exact equal-area overlap fraction; parallel published density reading retained"
FROZEN = ROOT / "outputs/长沙养老研究_第七轮/tables"
STABILITY = ROOT / "outputs/长沙养老研究_第八轮/tables/macro_origin_stability.csv"


def write_csv(frame, name):
    with tempfile.NamedTemporaryFile(dir=HERE, suffix=".csv", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        temporary.replace(HERE / name)
    finally:
        temporary.unlink(missing_ok=True)


def read_density():
    source = json.loads((HERE / "source_manifest.json").read_text())
    bands = []
    for group in ["age60_64", "age65above", "total_pop_sum"]:
        info = source["rasters"][group]
        path = HERE / info["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
        with Image.open(path) as src:
            bands.append(np.asarray(src, dtype=float))
        assert list(bands[-1].shape) == info["shape"]
    info = source["rasters"]["age60_64"]
    assert info["source_crs"] == "EPSG:4326"
    assert all(info["affine"] == m["affine"] for m in source["rasters"].values())
    a, b, c, d, e, f = info["affine"]
    assert a > 0 and e < 0 and b == d == 0
    values = np.stack(bands, axis=-1)[::-1].copy()
    valid = np.isfinite(values).all(axis=-1)
    assert (values[valid] >= 0).all()
    age_band_sum = values[..., :2][valid].sum(axis=1)
    assert np.all(age_band_sum <= values[..., 2][valid] + 1e-5)
    assert all(np.array_equal(np.isfinite(values[..., i]), valid) for i in range(3))
    # Ascending coordinates allow simple search into native rows and columns.
    nrow, ncol = valid.shape
    lon = c + a * np.arange(ncol + 1)
    lat = f + e * np.arange(nrow, -1, -1)
    trans = Transformer.from_crs(4326, AREA_CRS, always_xy=True)
    x = trans.transform(lon, np.full(lon.size, lat.mean()))[0]
    y = trans.transform(np.full(lat.size, lon.mean()), lat)[1]
    # Nodata is retained as a measured area for every geometry, never as zero density.
    return x, y, values, valid


def integrate(geometry, grid):
    """Exact partial-cell areas; full-cell shortcuts do not alter totals."""
    x, y, values, valid = grid
    if geometry.is_empty or geometry.area == 0:
        return [0.0] * len(MEASURES)
    xmin, ymin, xmax, ymax = geometry.bounds
    assert x[0] <= xmin <= xmax <= x[-1] and y[0] <= ymin <= ymax <= y[-1]
    left = max(0, x.searchsorted(xmin, side="right") - 1)
    right = min(len(x) - 1, x.searchsorted(xmax, side="left"))
    lower = max(0, y.searchsorted(ymin, side="right") - 1)
    upper = min(len(y) - 1, y.searchsorted(ymax, side="left"))
    rr, cc = np.mgrid[lower:upper, left:right]
    rr = rr.ravel()
    cc = cc.ravel()
    pixels = shapely.box(x[cc], y[rr], x[cc + 1], y[rr + 1])
    shapely.prepare(geometry)
    touched = shapely.intersects(geometry, pixels)
    if not touched.any():
        raise ValueError("Nonempty polygon has no raster overlap")
    rr, cc, pixels = rr[touched], cc[touched], pixels[touched]
    inside = shapely.covers(geometry, pixels)
    area = (x[cc + 1] - x[cc]) * (y[rr + 1] - y[rr])
    boundary = ~inside
    if boundary.any():
        area[boundary] = shapely.area(shapely.intersection(pixels[boundary], geometry))
    total = area.sum()
    if abs(total - geometry.area) > max(1e-4, geometry.area * 1e-9):
        raise ArithmeticError(f"Pixel areas differ from geometry area by {total-geometry.area}")
    ok = valid[rr, cc]
    rv, cv = rr[ok], cc[ok]
    cell_area = (x[cv + 1] - x[cv]) * (y[rv + 1] - y[rv])
    census_mass = (area[ok] / cell_area) @ values[rv, cv]
    density_mass = (area[ok] / 10_000.0) @ values[rv, cv]
    result = []
    for mass in [census_mass, density_mass]:
        result.extend([float(mass[0]), float(mass[1]), float(mass[0] + mass[1]), float(mass[2])])
    return result + [float(total), float(area[~ok].sum())]


def zonal(frame, grid):
    # R4 lines are straight in their UTM CRS. Densify before projection so
    # long circle boundaries preserve their original footprint.
    geometry = frame.geometry.segmentize(50).to_crs(AREA_CRS).make_valid()
    stats = [integrate(g, grid) for g in geometry]
    return pd.DataFrame(stats, columns=MEASURES, index=frame.index)


def main():
    grid = read_density()
    zones = gpd.read_file(
        SPATIAL / "exclusive_circle_zones.gpkg", layer="exclusive_circles"
    ).rename(columns={"circle_id": "case_id"})
    residential = gpd.read_file(SPATIAL / "macro_residential.gpkg", layer="residential_clip_250m")[
        ["grid_id", "geometry"]
    ].rename(columns={"grid_id": "origin_id"})
    parts = gpd.overlay(
        residential, zones[["case_id", "geometry"]], how="intersection", keep_geom_type=True
    )
    parts = parts[parts.area > 0].copy()
    parts["computed_utm_area_m2"] = parts.area
    results = (
        pd.concat([parts[KEY + ["computed_utm_area_m2"]], zonal(parts, grid)], axis=1)
        .groupby(KEY, as_index=False)
        .sum()
    )
    fixed = pd.read_csv(SPATIAL / "grid_circle_area_weights_250m.csv").rename(
        columns={"circle_id": "case_id"}
    )
    weights = fixed.merge(results, on=KEY, how="outer", validate="one_to_one", indicator=True)
    assert weights["_merge"].eq("both").all()
    max_error = (weights["residential_area_m2"] - weights["computed_utm_area_m2"]).abs().max()
    assert max_error < 1e-3
    weights = weights.drop(columns=["_merge", "computed_utm_area_m2"])
    # The R4 study uses a 1e-6 m² support tolerance. One touching boundary
    # (KF-QZH-1) leaves a 7e-10 m² floating-point sliver in the raw overlay.
    discarded = weights.loc[
        weights.residential_area_m2 <= 1e-6, KEY + ["residential_area_m2", "older_population"]
    ]
    weights = weights.loc[weights.residential_area_m2 > 1e-6].copy()
    weights = weights[KEY + ["district", "street", "residential_area_m2"] + MEASURES].sort_values(
        KEY
    )
    # Missing-covered area would make policy weighting incomplete. If any occurs,
    # retain its footprint in QA; do not pass partial counts to downstream models.
    assert weights["nodata_area_m2"].sum() < 0.01, "ASPECT has nodata inside selected residences"
    write_csv(weights, "case_origin_weights.csv")
    circle_weights = weights.groupby("case_id", as_index=False)[
        MEASURES + ["residential_area_m2"]
    ].sum()
    whole = pd.concat([zones[["case_id", "district", "street"]], zonal(zones, grid)], axis=1)
    circle = whole.merge(
        circle_weights,
        on="case_id",
        how="left",
        validate="one_to_one",
        suffixes=("_domain", "_residential"),
    )
    fill = [s + "_residential" for s in MEASURES] + ["residential_area_m2"]
    circle[fill] = circle[fill].fillna(0.0)
    circle["older_population_outside_residential"] = (
        circle.older_population_domain - circle.older_population_residential
    )
    assert circle.older_population_outside_residential.min() > -1e-3
    write_csv(circle, "circle_population_support.csv")
    by_origin = weights.groupby("origin_id", as_index=False)[
        MEASURES + ["residential_area_m2"]
    ].sum()
    baseline = pd.read_csv(
        FROZEN / "main_origins.csv",
        usecols=[
            "origin_id",
            "grid_size_m",
            "conditional_joint",
            "conditional_deficit",
            "residential_area_m2",
        ]
        + [f"covered_{f}" for f in "HMSG"],
    )
    baseline = baseline[baseline.grid_size_m.eq(250)].rename(
        columns={"residential_area_m2": "frozen_grid_area_m2"}
    )
    stability = pd.read_csv(STABILITY, usecols=["origin_id", "multiple_deficit_stability"])
    macro = by_origin.merge(baseline, on="origin_id", validate="one_to_one").merge(
        stability, on="origin_id", validate="one_to_one"
    )
    assert len(macro) == len(baseline) == 5938
    assert (macro.frozen_grid_area_m2 - macro.residential_area_m2).abs().max() < 0.001
    macro["D_ge2"] = macro.conditional_deficit.ge(2)
    macro["D_eq4"] = macro.conditional_deficit.eq(4)
    macro["one_missing"] = macro.conditional_deficit.eq(1)
    macro["persistent_D_ge2_R8_54"] = macro.multiple_deficit_stability.eq("all_conditions")
    write_csv(macro, "origin_weights_and_frozen_baseline.csv")
    rows = []
    fields = [f"covered_{f}" for f in "HMSG"] + [
        "conditional_joint",
        "D_ge2",
        "D_eq4",
        "one_missing",
        "persistent_D_ge2_R8_54",
    ]
    for var in (
        ["residential_area_m2", "all_age_population", "older_population"]
        + COUNTS
        + DENSITY_SENSITIVITY
    ):
        for col in fields:
            weights_col = macro[var]
            total = weights_col.sum()
            numerator = weights_col[macro[col]].sum()
            rows.append(
                {
                    "baseline_version": "R7_250m_0.8mps_15min_R8_stability",
                    "weight": var,
                    "metric": col,
                    "numerator": numerator,
                    "denominator": total,
                    "percent": 100 * numerator / total,
                }
            )
    write_csv(pd.DataFrame(rows), "macro_weighted_comparison.csv")
    # These four full district outlines all fit the frozen native raster window.
    # Boundaries are R2's approximate DataV-to-WGS84 transform and may differ
    # from 2020 census boundaries. This is a scale check, not cell validation.
    districts = gpd.read_file(ROOT / "work/round2/analysis/district_boundaries.gpkg")
    official = pd.read_csv(HERE / "district_2020_census.csv")
    districts = districts.merge(
        official, left_on="name", right_on="district", validate="one_to_one"
    )
    district_rows = zonal(districts, grid)
    benchmarks = []
    for idx, row in districts.iterrows():
        for variable in POPULATION_COLUMNS:
            official_count = row[variable]
            for reading, col in [
                ("cell_count", variable),
                ("density_per_ha", variable + "_density_reading"),
            ]:
                estimate = district_rows.loc[idx, col]
                benchmarks.append(
                    {
                        "district": row.district,
                        "variable": variable,
                        "census_2020": official_count,
                        "reading": reading,
                        "aspect_integral": estimate,
                        "relative_difference_percent": 100 * (estimate / official_count - 1),
                        "nodata_m2": district_rows.loc[idx, "nodata_area_m2"],
                        "district_publication": row.url,
                    }
                )
    write_csv(pd.DataFrame(benchmarks), "district_unit_check.csv")
    checks = {
        "method": AGING_UNIT,
        "raster_grid_crs": "EPSG:4326",
        "overlap_area_crs": "EPSG:6933",
        "weights_scale_m": 250,
        "house_area_field": "R4 EPSG:32649 exact polygon intersection area",
        "raw_utm_overlay_rows": len(results),
        "excluded_r4_numerical_slivers": discarded.to_dict("records"),
        "house_fragment_key_n": len(weights),
        "origin_n": len(macro),
        "circle_with_residential_area_n": len(circle_weights),
        "circle_domain_n": len(circle),
        "max_absolute_r4_area_difference_m2": float(max_error),
        "raster_nodata_area_within_residential_m2": float(weights.nodata_area_m2.sum()),
        "raster_nodata_area_within_domain_m2": float(circle.nodata_area_m2_domain.sum()),
        "support_domain_equal_area_km2": float(circle.zonal_area_m2_domain.sum() / 1e6),
        "residential_footprint_equal_area_km2": float(weights.zonal_area_m2.sum() / 1e6),
        "selected_residential_age60plus": float(weights.older_population.sum()),
        "full_domain_age60plus_known_pixels": float(circle.older_population_domain.sum()),
        "outside_selected_residential_age60plus_known_pixels": float(
            circle.older_population_outside_residential.sum()
        ),
        "outside_fraction_of_known_domain_age60plus": float(
            circle.older_population_outside_residential.sum() / circle.older_population_domain.sum()
        ),
        "zero_population_positive_area_origins": int(macro.older_population.eq(0).sum()),
        "zero_population_positive_area_circle_origin_pieces": int(
            weights.older_population.eq(0).sum()
        ),
    }
    assert (
        abs(
            checks["selected_residential_age60plus"]
            + checks["outside_selected_residential_age60plus_known_pixels"]
            - checks["full_domain_age60plus_known_pixels"]
        )
        < 1e-6
    )
    (HERE / "quality_checks.json").write_text(
        json.dumps(checks, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(checks, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
