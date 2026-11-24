"""Render Figures 4C–E and S2C–D and export their inputs from the spatial archive."""
import argparse
from pathlib import Path
import sys

import geopandas as gpd
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent / "work/round3/code"))
from run_analysis import augment, make_csr

DISTANCE = 720.0
CASE = "YH-LT-1"
INK, MUTED = "#192f39", "#485d66"
TEAL, RUST, AMBER = "#086e72", "#af532d", "#d18b30"
GREY, STREET = "#aab8bb", "#d2dad8"
mpl.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                     "pdf.fonttype": 42, "axes.labelcolor": INK,
                     "text.color": INK, "xtick.color": MUTED, "ytick.color": INK})


def prepare(service, origins, candidates, od, ceilings, names):
    cohort = origins.loc[~origins["covered_" + service]].copy()
    sites = candidates.loc[candidates.function.eq(service)].drop_duplicates("site_id").copy()
    pairs = od.loc[od.site_id.isin(sites.site_id) & od.distance_m.le(DISTANCE)]
    coords = np.column_stack([sites.geometry.x, sites.geometry.y])
    xy = cohort[["x_utm49n_m", "y_utm49n_m"]].to_numpy()
    cohort["nearest_direct_m"] = cKDTree(coords).query(xy)[0]
    cohort["direct"] = cohort.nearest_direct_m <= DISTANCE
    cohort["network"] = cohort.origin_id.isin(pairs.origin_id)
    assert not (cohort.network & ~cohort.direct).any()
    cohort["direct_people"] = cohort.older_population * cohort.direct
    cohort["network_people"] = cohort.older_population * cohort.network
    summary = cohort.groupby("case_id").agg(initially_unserved=("older_population", "sum"),
                       direct_people=("direct_people", "sum"), network_people=("network_people", "sum"))
    check = ceilings.loc[ceilings.function.eq(service)].set_index("scope")
    np.testing.assert_allclose(summary.initially_unserved, check.loc[summary.index, "initial_uncovered"], atol=1e-5)
    np.testing.assert_allclose(summary.network_people, check.loc[summary.index, "attainable_population"], atol=1e-5)
    np.testing.assert_allclose(summary.network_people.sum(), check.at["ALL16", "attainable_population"], atol=1e-5)
    summary["eligible_sites"] = check.loc[summary.index, "eligible_sites"].astype(int)
    summary["circle"] = names.reindex(summary.index)
    assert summary.circle.notna().all()
    summary["direct_pct"] = 100 * summary.direct_people / summary.initially_unserved
    summary["network_pct"] = 100 * summary.network_people / summary.initially_unserved
    return cohort, sites, pairs, summary


def local_route(edges, local, venue):
    """Route all initially unserved Lituo origins in the radius on the full graph."""
    pts = local.loc[local.direct].copy()
    centre = venue.geometry.iloc[0]
    pts["line_m"] = pts.distance(centre)
    pts = pts.sort_values("line_m")
    target = venue.assign(record_id=venue.site_id, function="site")
    xy = {}
    for edge in edges.itertuples():
        xy[edge.u] = edge.geometry.coords[0]
        xy[edge.v] = edge.geometry.coords[-1]
    coords, arcs, nodes, _, snaps = augment(edges, xy, pts, target)
    reverse = make_csr(arcs, len(coords)).T.tocsr()
    dest = nodes[target.site_id.iat[0]]
    dist, previous = dijkstra(reverse, directed=True, indices=dest, return_predecessors=True)
    pts["route_m"] = [dist[nodes[oid]] for oid in pts.origin_id]
    assert len(pts) == 20 and not pts.route_m.le(DISTANCE).any()
    np.testing.assert_allclose(pts.connector_m, snaps.set_index("object_id").loc[pts.origin_id, "connector_m"], atol=.001)
    np.testing.assert_allclose(venue.connector_m.iat[0], snaps.set_index("object_id").at[target.site_id.iat[0], "connector_m"], atol=.001)
    nearest = pts.iloc[0]
    path = [nodes[nearest.origin_id]]
    while path[-1] != dest:
        predecessor = previous[path[-1]]
        if predecessor < 0:
            raise ValueError("The selected origin has no path to the venue")
        path.append(predecessor)
    route = coords[path]
    np.testing.assert_allclose(np.linalg.norm(np.diff(route, axis=0), axis=1).sum(), nearest.route_m, atol=.001)
    np.testing.assert_allclose(pts.line_m, pts.nearest_direct_m, rtol=0, atol=.001)
    return pts, route


def scale_bar(ax, metres):
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    x, y = x0 + .055*(x1-x0), y0 + .07*(y1-y0)
    ax.plot([x, x+metres], [y, y], color=INK, lw=2, solid_capstyle="butt", zorder=12)
    ax.text(x+metres/2, y+.017*(y1-y0), f"{metres} m", fontsize=7.3, ha="center", zorder=12,
            bbox=dict(facecolor="white", edgecolor="none", pad=.5, alpha=.85))


def base_map(ax, edges, zone, extent, scale):
    xmin, ymin, xmax, ymax = extent
    frame = box(*extent)
    ax.set_facecolor("#fcfcfa")
    gpd.GeoSeries([zone], crs=32649).plot(ax=ax, facecolor="#f1f3ef", edgecolor="#92a19e", lw=.8, zorder=1)
    indices = edges.sindex.query(frame, predicate="intersects")
    subset = edges.iloc[indices]
    ax.add_collection(LineCollection([np.asarray(v.coords) for v in subset.geometry],
                                      colors=STREET, linewidths=.48, zorder=2))
    ax.set(xlim=(xmin, xmax), ylim=(ymin, ymax), xticks=[], yticks=[], xlabel="", ylabel="")
    ax.set_aspect("equal")
    for spine in ax.spines.values():
        spine.set_color("#cfd8d6")
        spine.set_linewidth(.65)
    scale_bar(ax, scale)


def export(fig, output, stem):
    for ext in ("pdf", "png"):
        fig.savefig(output / f"{stem}.{ext}", dpi=240, facecolor="white")
    plt.close(fig)


def header(fig, title, subtitle):
    fig.text(.04, .943, title, fontsize=17, weight="bold", va="baseline")
    fig.text(.04, .902, subtitle, fontsize=9.6, color=MUTED)


def radius_figure(cohort, sites, summary, edges, zones, output):
    local = cohort.loc[cohort.case_id.eq(CASE)]
    venue = sites.loc[sites.case_id.eq(CASE)]
    assert len(venue) == 1
    routed, route = local_route(edges, local, venue)
    nearest = routed.iloc[0]
    p, start = venue.geometry.iloc[0], nearest.geometry
    fig = plt.figure(figsize=(11.5, 6.75))
    header(fig, "Walking access to candidate daytime activity venues",
           "Changsha  ·  46 eligible sites  ·  Residents aged 60+ without existing activity access  ·  720 m threshold")
    fig.text(.04, .825, "C   One candidate centre in Lituo", fontsize=11.4, weight="bold")
    fig.text(.555, .825, "D   Reach by residents’ home circle", fontsize=11.4, weight="bold")
    ax = fig.add_axes([.04, .252, .439, .535])
    extent = (p.x-815, p.y-770, p.x+1040, p.y+805)
    base_map(ax, edges, zones.at[CASE, "geometry"], extent, 250)
    ax.add_patch(Circle((p.x,p.y), DISTANCE, facecolor="#f6e6d8", alpha=.28, edgecolor="none", zorder=1.3))
    ax.add_patch(Circle((p.x,p.y), DISTANCE, fill=False, edgecolor=RUST, lw=1.4, ls=(0,(4,3)), zorder=3))
    for hit, colour, size in [(False, GREY, 11),(True, RUST, 24)]:
        pts = local.loc[local.direct.eq(hit)]
        ax.scatter(pts.geometry.x, pts.geometry.y, s=size, c=colour, edgecolors="white", linewidths=.45, zorder=4)
    ax.plot([p.x,start.x],[p.y,start.y],lw=1.5,color=RUST,ls=(0,(1.5,1.4)),zorder=5)
    ax.plot(*route.T, color="white", lw=4.6, solid_capstyle="round", zorder=5.1)
    ax.plot(*route[1:-1].T, color=TEAL, lw=2.5, solid_capstyle="round", zorder=5.2)
    for connector in (route[:2], route[-2:]):
        ax.plot(*connector.T, color=TEAL, lw=2.15, ls=(0,(1,1.2)), zorder=5.2)
    ax.scatter(p.x,p.y,s=63,marker="D",c=INK,edgecolor="white",lw=1.0,zorder=6)
    ax.scatter(start.x,start.y,s=62,facecolor="white",edgecolor=RUST,lw=2,zorder=6)
    annotation = dict(arrowprops=dict(arrowstyle="-", color=MUTED, lw=.65, shrinkB=4),
                      fontsize=7.8, color=INK, bbox=dict(fc="white",ec="none",alpha=.93,pad=1.2), zorder=7)
    ax.annotate("Candidate centre",(p.x,p.y),xytext=(p.x-560,p.y+85),**annotation)
    ax.annotate("Nearest unserved origin",(start.x,start.y),xytext=(p.x+110,p.y-266),**annotation)
    ax.text(p.x-505,p.y-505,"720 m radius",color=RUST,fontsize=8,
            bbox=dict(fc="white",ec="none",alpha=.84,pad=.5),zorder=6)
    ax.text(.955,.94,"N",transform=ax.transAxes,ha="center",fontsize=7.5,weight="bold")
    ax.annotate("",xy=(.955,.93),xytext=(.955,.86),xycoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>",lw=.8,color=INK))
    leg = [
        Line2D([],[],color=RUST,ls=(0,(1.5,1.4)),lw=1.5,label=f"Direct line  {nearest.line_m:,.0f} m"),
        Line2D([],[],color=TEAL,lw=2.6,label=f"Shortest mapped walk  {nearest.route_m:,.0f} m")]
    fig.legend(handles=leg,loc="upper left",bbox_to_anchor=(.04,.241),frameon=False,ncol=2,
               fontsize=8.5,handlelength=2.3,columnspacing=1.4,borderaxespad=0)
    near = local.loc[local.direct]
    fig.text(.04,.171,f"{len(near)} previously unserved origins ({near.older_population.sum():.1f} residents) fall inside the radius.",fontsize=8.9)
    fig.text(.04,.136,"None can reach the centre within a 720 m walk.",fontsize=9.2)
    fig.text(.04,.105,"Dotted teal segments connect the origin and venue to the street map.",fontsize=7.8,color=MUTED)

    b = fig.add_axes([.67,.244,.291,.51])
    valid = summary[summary.eligible_sites.gt(0)].copy()
    valid["difference"] = valid.direct_pct - valid.network_pct
    valid = valid.sort_values("difference",ascending=False)
    all_direct = 100*summary.direct_people.sum()/summary.initially_unserved.sum()
    all_network = 100*summary.network_people.sum()/summary.initially_unserved.sum()
    labels = valid.circle.tolist() + ["All 16 circles"]
    pos = list(range(len(valid))) + [len(valid)+.55]
    direct = valid.direct_pct.tolist() + [all_direct]
    network = valid.network_pct.tolist() + [all_network]
    b.axhspan(pos[-1]-.48,pos[-1]+.48,color="#edf2f0",zorder=0)
    for j,(y,a,d) in enumerate(zip(pos,network,direct)):
        b.plot([a,d],[y,y],color="#a6b4b4",lw=1.7,zorder=2)
        b.scatter(d,y,s=31,facecolor="white",edgecolor=RUST,linewidth=1.5,zorder=4)
        b.scatter(a,y,s=24,facecolor=TEAL,edgecolor=TEAL,linewidth=.7,zorder=4)
        if j == len(labels)-1:
            b.text(a-3,y,f"{a:.1f}%",va="center",ha="right",fontsize=8.3,color=TEAL,weight="bold")
            b.text(d+3,y,f"{d:.1f}%",va="center",fontsize=8.3,color=RUST,weight="bold")
    b.set(xlim=(-1.5,101),ylim=(pos[-1]+.55,-.6),yticks=pos,yticklabels=labels,xticks=[0,25,50,75,100])
    b.set_xlabel("Initially unserved residents reached (%)",fontsize=8.4,labelpad=4)
    b.set_axisbelow(True);b.grid(axis="x",lw=.5,color="#e0e5e4")
    b.tick_params(axis="y",length=0,pad=6,labelsize=8.1)
    b.tick_params(axis="x",length=2,pad=2,labelsize=8)
    for tick in b.get_yticklabels():
        if tick.get_text() in ("Lituo","All 16 circles"):
            tick.set_weight("bold")
    for side in ("left","right","top"): b.spines[side].set_visible(False)
    b.spines["bottom"].set_color("#a8b5b6")
    keys=[Line2D([],[],marker="o",ls="none",color=TEAL,markersize=5,label="Street network"),
          Line2D([],[],marker="o",ls="none",mfc="white",mec=RUST,mew=1.5,markersize=6,label="Straight line")]
    fig.legend(handles=keys,bbox_to_anchor=(.55,.80),loc="upper left",frameon=False,ncol=2,fontsize=8.5,
               columnspacing=1.1,borderaxespad=0,handletextpad=.35)
    fig.text(.555,.132,"No eligible venue in Jinshanqiao, Nantuo, Yueliangdao or Yuelu.",fontsize=8.0,color=MUTED)
    fig.text(.555,.103,"These four circles are included in the pooled percentage.",fontsize=8.0,color=MUTED)
    fig.text(.04,.032,"Dots in C represent residential grid origins. Counts use ASPECT 2020 population weights. "
             "Walking streets © OpenStreetMap contributors, 2026.",fontsize=7.5,color=MUTED,
             url="https://www.openstreetmap.org/copyright")
    export(fig, output, "figS2c-d_activity_radius_and_network")
    return routed.set_index("origin_id").route_m.rename("lituo_route_m")


def residual_figure(cohort, sites, pairs, summary, zones, roads, tables, output):
    distributions = pd.read_csv(tables / "siting_pooled_distribution.csv")
    allocation = distributions.query("registry=='strict' and function=='M' and menu=='expanded' and site_budget==10")
    plan_ids = allocation.plan_id.unique()
    assert len(plan_ids) == 1
    plan_sites = pd.read_csv(tables / "siting_plan_sites.csv")
    chosen = set(plan_sites.loc[plan_sites.plan_id.eq(plan_ids[0]), "site_id"])
    assert len(chosen) == 10 and chosen.issubset(set(sites.site_id))
    cohort = cohort.copy()
    cohort["selected"] = cohort.origin_id.isin(pairs.loc[pairs.site_id.isin(chosen),"origin_id"])
    assert not (cohort.selected & ~cohort.network).any()
    category = np.select([cohort.selected, cohort.network],["Planned","Other"],default="Beyond")
    cohort["category"] = category
    counts = cohort.pivot_table(index="case_id",columns="category",values="older_population",
                                aggfunc="sum",fill_value=0)[["Planned","Other","Beyond"]]
    np.testing.assert_allclose(counts["Planned"],allocation.set_index("case_id").loc[counts.index,"newly_covered"],atol=1e-5)
    np.testing.assert_allclose(counts[["Planned","Other"]].sum(axis=1),summary.loc[counts.index,"network_people"],atol=1e-5)
    np.testing.assert_allclose(counts.sum(axis=1),summary.loc[counts.index,"initially_unserved"],atol=1e-5)
    cases = [counts.Planned.idxmax(), counts.loc[counts.Planned.eq(0),"Other"].idxmax()]
    colors = {"Planned":TEAL,"Other":AMBER,"Beyond":GREY}
    fig = plt.figure(figsize=(10.8,6.85))
    header(fig,"Where do gaps remain after adding ten meal sites?",
           "A representative maximum-coverage plan  ·  Previously unserved residents aged 60+  ·  720 m walk")
    width, height = 3650, 2510
    for j,case in enumerate(cases):
        x = .04 + j*.478
        ax = fig.add_axes([x,.414,.444,.398])
        name = summary.at[case,"circle"]
        fig.text(x,.839,["C","D"][j]+"   "+name,fontsize=11.7,weight="bold")
        local = cohort.loc[cohort.case_id.eq(case)]
        effective = pairs.loc[pairs.origin_id.isin(local.origin_id) & pairs.site_id.isin(chosen),"site_id"].unique()
        local_chosen = sites.loc[sites.site_id.isin(effective)]
        zone = zones.at[case,"geometry"]
        bounds = zone.bounds
        X = (bounds[0]+bounds[2])/2; Y=(bounds[1]+bounds[3])/2
        extent=(X-width/2,Y-height/2,X+width/2,Y+height/2)
        assert box(*extent).covers(zone) and all(box(*extent).covers(p) for p in local_chosen.geometry)
        base_map(ax,roads,zone,extent,500)
        for key, size in [("Beyond",10),("Other",15),("Planned",15)]:
            group=local[local.category.eq(key)]
            ax.scatter(group.geometry.x,group.geometry.y,s=size,c=colors[key],edgecolors="white",lw=.38,zorder=4)
        if not local_chosen.empty:
            ax.scatter(local_chosen.geometry.x,local_chosen.geometry.y,s=45,marker="D",c=INK,
                       edgecolors="white",lw=.75,zorder=5)
        label = (f"Largest ten-site gain  ·  {counts.at[case, 'Planned']:,.0f} residents" if j == 0
                 else f"No gain in the plan  ·  {counts.at[case, 'Other']:,.0f} residents within reach of other sites")
        fig.text(x,.392,label,fontsize=8.6,color=MUTED)
    handles=[Line2D([],[],ls="none",marker="o",mfc=colors[k],mec=colors[k],markersize=6,label=v)
             for k,v in [("Planned","Reached by the ten-site plan"),("Other","Reachable by other sites only"),("Beyond","Beyond all eligible sites")]]
    handles.append(Line2D([],[],ls="none",marker="D",color=INK,markersize=5,label="Selected site"))
    fig.legend(handles=handles,ncol=4,frameon=False,loc="center left",bbox_to_anchor=(.035,.337),
               fontsize=8.0,columnspacing=1.08,handletextpad=.24,borderaxespad=0)
    fig.text(.04,.273,"E   Share of each initial meal-access gap",fontsize=11.4,weight="bold")
    totals=counts.loc[cases].copy()
    totals.loc["ALL16"]=counts.sum()
    names=[summary.at[c,"circle"] for c in cases]+["All 16 circles"]
    ax=fig.add_axes([.17,.081,.632,.158]); ax.set_xlim(0,100);ax.set_ylim(2.55,-.5)
    for i,(_,row) in enumerate(totals.iterrows()):
        start=0.
        for k in ("Planned","Other","Beyond"):
            pct=100*row[k]/row.sum()
            ax.barh(i,pct,left=start,height=.62,color=colors[k],edgecolor="white",linewidth=.65)
            if pct>=6:
                ax.text(start+pct/2,i,f"{pct:.1f}%",ha="center",va="center",fontsize=8.2,
                        color=("white" if k=="Planned" else INK),weight="bold")
            elif pct>0:
                ax.annotate(f"{pct:.1f}%",(start+pct/2,i-.3),xytext=(0,2),
                            textcoords="offset points",ha="center",va="bottom",fontsize=7.2,color=MUTED)
            start+=pct
        ax.text(104,i,f"{row.sum():,.0f}",va="center",fontsize=9.2,clip_on=False)
    ax.set(yticks=range(3),yticklabels=names,xticks=[])
    ax.tick_params(left=False,pad=4)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.get_yticklabels()[-1].set_weight("bold")
    fig.text(.825,.249,"Initially unserved",fontsize=8.0,color=MUTED)
    fig.text(.04,.029,"Dots represent residential origins. Bars weight origins by residents aged 60+. The pooled row includes all 16 circles. "
             "Streets © OpenStreetMap contributors.",fontsize=7.4,color=MUTED,url="https://www.openstreetmap.org/copyright")
    export(fig, output, "fig4c-e_meal_plan_and_remaining_gaps")
    return counts.rename(columns={"Planned":"meal_ten_site_people","Other":"meal_other_only_people","Beyond":"meal_beyond_sites_people"})


def main(project, output):
    tables = project / "outputs/长沙养老研究_第十九轮/tables"
    spatial = project / "work/round4"
    output.mkdir(parents=True, exist_ok=True)
    o = pd.read_csv(tables / "routing_origins.csv").query("scenario=='strict'").copy()
    assert len(o)==5959 and o.origin_id.is_unique
    origins=gpd.GeoDataFrame(o,geometry=gpd.points_from_xy(o.x_utm49n_m,o.y_utm49n_m),crs=32649)
    c=pd.read_csv(tables / "routing_candidates.csv").query("scenario=='strict'").copy()
    sites=gpd.GeoDataFrame(c,geometry=gpd.points_from_xy(c.lon_wgs84,c.lat_wgs84),crs=4326).to_crs(32649)
    od=pd.read_csv(tables / "routing_reachable_od.csv")
    assert not od.duplicated(["origin_id","site_id"]).any() and od.distance_m.le(DISTANCE).all()
    ceilings=pd.read_csv(tables / "siting_ceiling.csv").query("registry=='strict' and menu=='expanded'")
    names=pd.read_csv(Path(__file__).resolve().parents[1] / "data/figure_data/fig4_spatial_distribution_data.csv")
    names=names[["case_id","circle_en"]].drop_duplicates().set_index("case_id").circle_en
    zones=gpd.read_file(spatial/"spatial/exclusive_circle_zones.gpkg",layer="exclusive_circles").to_crs(32649).set_index("circle_id")
    roads=gpd.read_file(spatial/"analysis/macro_network.gpkg",layer="walking_segments")
    offsite = sites.loc[sites.pool_type == "offsite"].drop_duplicates(["case_id", "site_id"])
    boundary = offsite[["case_id", "site_id"]].copy()
    boundary["site_outside_circle"] = [
        not zones.at[case, "geometry"].covers(point) for case, point in zip(offsite.case_id, offsite.geometry)
    ]
    boundary.sort_values(["case_id", "site_id"]).to_csv(output / "offsite_circle_membership.csv", index=False)
    a,ac,_,s=prepare("S",origins,sites,od,ceilings,names)
    m,mc,mp,ms=prepare("M",origins,sites,od,ceilings,names)
    assert len(ac)==46 and len(mc)==199
    routes = radius_figure(a,ac,s,roads,zones,output)
    distances = a.set_index("origin_id")[["nearest_direct_m"]].join(routes).sort_index()
    distances.to_csv(output / "activity_origin_distances.csv", float_format="%.7f")
    meals=residual_figure(m,mc,mp,ms,zones,roads,tables,output)
    results=s[["circle","eligible_sites","initially_unserved","direct_people","network_people"]].copy()
    results.columns=["circle","activity_eligible_sites","activity_initially_unserved","activity_direct_people","activity_network_people"]
    results=results.join(meals)
    pooled=results.drop(columns="circle").sum()
    assert pooled.activity_eligible_sites==len(ac)
    results.loc["ALL16",pooled.index]=pooled
    results.loc["ALL16","circle"]="All 16 circles"
    for measure in ("direct","network"):
        results["activity_"+measure+"_pct"]=100*results["activity_"+measure+"_people"]/results.activity_initially_unserved
    results.to_csv(output/"circle_figure_data.csv",float_format="%.7f",index_label="case_id")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="Root of the full spatial archive")
    parser.add_argument("--output", type=Path, required=True, help="Destination for maps and numerical extracts")
    args = parser.parse_args()
    main(args.project, args.output)
