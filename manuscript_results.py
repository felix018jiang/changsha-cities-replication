"""Numerical inputs to Figures 4C–E, S2C–D and Tables S2, S4 and S5."""
from pathlib import Path

import numpy as np
import pandas as pd


DATA = Path(__file__).resolve().parent / "data"
DISTANCE_M = 720
LITUO = "YH-LT-1"


def circle_results(primary):
    origins = pd.read_csv(primary / "routing_origins.csv").query("scenario == 'strict'")
    candidates = pd.read_csv(primary / "routing_candidates.csv").query("scenario == 'strict'")
    od = pd.read_csv(primary / "routing_reachable_od.csv").query("distance_m <= @DISTANCE_M")
    allocation = pd.read_csv(primary / "siting_pooled_distribution.csv").query(
        "registry == 'strict' and function == 'M' and menu == 'expanded' and site_budget == 10"
    )
    plan_id, = allocation.plan_id.unique()
    plans = pd.read_csv(primary / "siting_plan_sites.csv")
    chosen = plans.loc[plans.plan_id == plan_id, "site_id"]
    meal_sites = candidates.loc[candidates.function == "M", "site_id"]
    if chosen.nunique() != 10 or not chosen.isin(meal_sites).all():
        raise ValueError("Expected ten eligible sites in the representative meal plan")

    meals = origins.loc[~origins.covered_M].set_index("origin_id", verify_integrity=True)
    planned = meals.index.isin(od.loc[od.site_id.isin(chosen), "origin_id"])
    reachable = meals.index.isin(od.loc[od.site_id.isin(meal_sites), "origin_id"])
    meal_counts = pd.DataFrame({
        "meal_ten_site_people": meals.older_population * planned,
        "meal_other_only_people": meals.older_population * (reachable & ~planned),
        "meal_beyond_sites_people": meals.older_population * ~reachable,
    }).groupby(meals.case_id).sum()
    gain = allocation.set_index("case_id", verify_integrity=True).newly_covered
    np.testing.assert_allclose(meal_counts.meal_ten_site_people, gain.loc[meal_counts.index],
                               rtol=0, atol=1e-6)

    activity = origins.loc[~origins.covered_S].set_index("origin_id", verify_integrity=True)
    distances = pd.read_csv(DATA / "activity_origin_distances.csv").set_index(
        "origin_id", verify_integrity=True
    )
    if not activity.index.sort_values().equals(distances.index.sort_values()):
        raise ValueError("The straight-line and walking estimates must use the same activity cohort")
    activity = activity.join(distances)
    if not np.isfinite(activity.nearest_direct_m).all() or (activity.nearest_direct_m < 0).any():
        raise ValueError("Invalid straight-line distance")
    direct = activity.nearest_direct_m <= DISTANCE_M
    activity_sites = candidates.loc[candidates.function == "S"]
    network = activity.index.isin(od.loc[od.site_id.isin(activity_sites.site_id), "origin_id"])
    if (network & ~direct).any():
        raise ValueError("A reachable walking destination lies outside the straight-line radius")

    summary = pd.DataFrame({
        "activity_initially_unserved": activity.older_population,
        "activity_direct_people": activity.older_population * direct,
        "activity_network_people": activity.older_population * network,
    }).groupby(activity.case_id).sum()
    site_counts = activity_sites.groupby("case_id").site_id.nunique()
    summary.insert(0, "activity_eligible_sites", site_counts.reindex(summary.index, fill_value=0))
    summary = summary.join(meal_counts, validate="one_to_one")
    summary.loc["ALL16"] = summary.sum()
    names = allocation.set_index("case_id").circle
    names.loc["ALL16"] = "All 16 circles"
    summary.insert(0, "circle", names.loc[summary.index])
    for mode in ("direct", "network"):
        summary[f"activity_{mode}_pct"] = (
            100 * summary[f"activity_{mode}_people"] / summary.activity_initially_unserved
        )

    local = activity.case_id.eq(LITUO) & direct
    if not activity.lituo_route_m.notna().equals(local):
        raise ValueError("Expected full route distances for all Lituo origins within the radius")
    nearby = activity.loc[local]
    if (nearby.lituo_route_m < nearby.nearest_direct_m).any():
        raise ValueError("A Lituo walking route is shorter than its straight line")
    nearest = nearby.loc[nearby.nearest_direct_m.idxmin()]
    route = dict(radius_origins=len(nearby), radius_population=float(nearby.older_population.sum()),
                 walkable_origins=int((nearby.lituo_route_m <= DISTANCE_M).sum()),
                 nearest_origin=nearest.name, direct_m=float(nearest.nearest_direct_m),
                 route_m=float(nearest.lituo_route_m))
    return summary, route


def offsite_reach(primary):
    keys = ["case_id", "site_id"]
    offsite = pd.read_csv(primary / "routing_candidates.csv").query(
        "scenario == 'strict' and pool_type == 'offsite'"
    ).set_index(keys, verify_integrity=True)
    boundary = pd.read_csv(DATA / "offsite_circle_membership.csv").set_index(keys, verify_integrity=True)
    if not offsite.index.sort_values().equals(boundary.index.sort_values()):
        raise ValueError("Boundary flags must cover every eligible off-site venue")
    gains = pd.read_csv(primary / "routing_gains.csv").query("scenario == 'strict' and older_population > 0")
    effective = boundary.loc[boundary.index.isin(gains.set_index(keys).index)]
    return dict(eligible=len(boundary), effective=len(effective),
                effective_outside_circle=int(effective.site_outside_circle.sum()))


def supplement_tables(primary, supplementary):
    """Use stable site IDs for the manuscript's representative address combinations."""
    walking = pd.read_csv(supplementary / "walking_thresholds.csv").query("service == 'meals'")
    candidates = pd.read_csv(primary / "routing_candidates.csv").query(
        "scenario == 'strict' and function == 'M'"
    ).set_index("site_id", verify_integrity=True)
    sites = []
    for row in walking.loc[walking.threshold_m == DISTANCE_M].itertuples():
        for site in row.selected_site_ids.split("|"):
            sites.append(dict(menu=row.menu, case_id=candidates.at[site, "case_id"], site_id=site))

    removals = pd.read_csv(supplementary / "meal_site_removals.csv", keep_default_na=False)
    featured = ["none", "osm:node:7071192121", "chain:bawan:142170042b7bd3", "osm:node:7071192140"]
    exclusions = removals.set_index("removed_site_id").loc[featured, [
        "removed_site_label", "minimum_sites", "best_at_previous", "best_at_minimum"
    ]].reset_index()
    walking_rows = []
    for distance, group in walking.groupby("threshold_m", sort=False):
        group = group.set_index("menu", verify_integrity=True)
        row = group.loc["stations"]
        walking_rows.append(dict(speed_mps=row.speed_mps, threshold_m=distance,
                                 initial_unserved=row.initial_unserved, target_people=row.target_people,
                                 stations=group.at["stations", "minimum_sites"],
                                 operator=group.at["operator", "minimum_sites"],
                                 all_sites=group.at["all", "minimum_sites"]))
    return {"tableS2.csv": pd.DataFrame(sites), "tableS4.csv": exclusions,
            "tableS5.csv": pd.DataFrame(walking_rows)}
