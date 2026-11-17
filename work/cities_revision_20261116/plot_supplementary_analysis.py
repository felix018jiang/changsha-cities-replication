"""Draw Figures 5 and S3 from the supplementary analysis CSVs.

Run after supplementary_analysis.py, using the project's GIS Python.
Use --tables and --output to draw from a different results directory.
"""
import argparse
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "changsha-figure-cache"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, StrMethodFormatter
from matplotlib.transforms import blended_transform_factory
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs" / "Cities_补充计算与图表"
INK = "#1c343c"
MUTED = "#53636a"
GRID = "#dce3e4"
STATIONS = "#2e5e7a"
EXPANDED = "#aa5129"
PROTECTED = "#276a62"
OPERATOR = "#55775e"
ACTIVITY_PLANS = {
    "station_only": "Stations only",
    "expanded": "Expanded, no circle constraint",
    "min_7_circles": "Expanded, at least 7 circles",
    "min_8_circles": "Expanded, at least 8 circles",
    "circle_floors": "Expanded, protect each circle",
}
MEAL_MENUS = {
    "stations": ("Stations only", STATIONS),
    "operator": ("+ operator-listed restaurants", OPERATOR),
    "all": ("+ all screened restaurants", EXPANDED),
}
REMOVALS = {
    "No removal": "All sites retained",
    "Koufulou": "Remove Koufulou",
    "Laoshi Noodle Shop": "Remove Laoshi Noodle Shop",
    "Bawan Rice Bowl (Shaoshan South Road)": "Remove Bawan Rice Bowl\n(Shaoshan South Road)",
}


def save_figure(fig, output, stem):
    fig.savefig(output / (stem + ".png"), dpi=450, facecolor="white")
    fig.savefig(output / (stem + ".pdf"), facecolor="white",
                metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)


def row_axis(fig, box, rows, xmin, xmax):
    """Horizontal quantitative axis with labels and reference columns outside it."""
    ax = fig.add_axes(box)
    ax.set(xlim=(xmin, xmax), ylim=(len(rows) - .5, -.5), yticks=[])
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="x", length=2.5, width=.6, pad=3, colors=MUTED)
    ax.grid(axis="x", color=GRID, lw=.6)
    ax.set_axisbelow(True)
    return ax, blended_transform_factory(fig.transFigure, ax.transData)


def heading(fig, y, text):
    fig.text(.028, y, text, size=10.7, weight="bold", color=INK, va="center")


def activity_figure(tables, output):
    totals = pd.read_csv(tables / "activity_plans.csv").set_index("scenario").loc[list(ACTIVITY_PLANS)]
    circles = pd.read_csv(tables / "activity_circles.csv")
    gains = circles.pivot(index="circle", columns="scenario", values="newly_covered")
    if len(gains) != 16 or gains[list(ACTIVITY_PLANS)].isna().any().any():
        raise ValueError("Expected a complete 16-circle activity matrix")
    if not np.allclose(gains[totals.index].sum(), totals.newly_covered):
        raise ValueError("Circle counts do not sum to the corresponding plan totals")

    changes = gains[["expanded", "circle_floors"]].sub(gains["station_only"], axis=0)
    changed = changes.loc[changes.abs().max(axis=1) > 1e-6].sort_values("expanded", ascending=False)
    height = 5.55 + max(0, len(changed) - 5) * .16
    fig = plt.figure(figsize=(6.55, height))
    fig.text(.028, .974, "Allocating ten daytime activity sites", size=14, weight="bold", va="top")
    fig.text(.028, .92, "Strict registry  ·  16 living circles  ·  720 m walking threshold",
             size=8.6, color=MUTED)
    heading(fig, .857, "A   Coverage under five allocation rules")

    delta = totals.newly_covered - totals.at["station_only", "newly_covered"]
    ax, trans = row_axis(fig, [.453, .548, .282, .222], totals, 0, max(delta) * 1.12)
    ax.set_xticks([0, 100, 200, 300])
    ax.set_xlabel("Gain over stations only (residents)", fontsize=8, labelpad=4)
    fig.text(.028, .795, "Allocation rule", size=8.3, weight="bold", color=MUTED)
    fig.text(.838, .795, "Total reached", size=8.3, weight="bold", ha="center", color=MUTED)
    fig.text(.951, .795, "Circles", size=8.3, weight="bold", ha="center", color=MUTED)
    for i, (key, row) in enumerate(totals.iterrows()):
        color = STATIONS if key == "station_only" else EXPANDED if key == "expanded" else PROTECTED
        ax.plot([0, delta[key]], [i, i], color=color, lw=2.2, solid_capstyle="round")
        ax.plot(delta[key], i, marker="o", ms=4.6, color=color, clip_on=False)
        ax.text(.028, i, ACTIVITY_PLANS[key], transform=trans, va="center", fontsize=8.7)
        ax.text(.838, i, f"{row.newly_covered:,.1f}", transform=trans, ha="center", va="center",
                fontsize=8.9, color=color)
        ax.text(.951, i, f"{int(row.benefiting_circles)}", transform=trans,
                ha="center", va="center", fontsize=8.9)
    heading(fig, .418, "B   Change in residents reached within each affected circle")
    markers = [("expanded", "Unconstrained expansion", EXPANDED, "o"),
               ("circle_floors", "Each circle protected", PROTECTED, "s")]
    legend = [Line2D([], [], marker=m, color=c, lw=0, markersize=5.2, label=label)
              for _, label, c, m in markers]
    fig.legend(handles=legend, loc="center left", bbox_to_anchor=(.024, .371),
               frameon=False, ncol=2, columnspacing=2, handlelength=1.1, fontsize=8.4)
    low = min(-10, changed.min().min() * 1.36)
    high = max(10, changed.max().max() * 1.1)
    ax, trans = row_axis(fig, [.247, .118, .71, .21], changed, low, high)
    ticks = MaxNLocator(nbins=6, integer=True).tick_values(low, high)
    visible = ticks[(ticks >= low) & (ticks <= high)]
    if low < 0 and visible.min() >= 0:
        negative = MaxNLocator(nbins=2, integer=True).tick_values(low, 0)
        visible = np.r_[negative[(negative >= low) & (negative < 0)][-1:], visible]
    ax.set_xticks(visible)
    ax.axvline(0, color=MUTED, lw=1.1)
    ax.set_xlabel("Difference from the stations-only allocation (residents)", fontsize=8.6, labelpad=4)
    for i, (circle, row) in enumerate(changed.iterrows()):
        ax.text(.028, i, circle, transform=trans, ha="left", va="center", fontsize=8.8)
        ax.plot([row.expanded, row.circle_floors], [i, i], color="#afbabc", lw=1.1)
        for key, _, c, m in markers:
            ax.scatter(row[key], i, marker=m, s=27, facecolor=c, edgecolor="white",
                       linewidth=.5, zorder=3)
    fig.text(.028, .016, f"Both allocations give the same gain as stations only in the other "
             f"{len(gains) - len(changed)} circles.", fontsize=8.2, color=MUTED)
    save_figure(fig, output, "Fig5_activity_coverage_guarantees")


def meal_figure(tables, output):
    removal_table = pd.read_csv(tables / "meal_site_removals.csv")
    removals = removal_table.loc[removal_table.removed_site_label.isin(REMOVALS)]
    removals = removals.set_index("removed_site_label").loc[list(REMOVALS)]
    if len(removals) != len(REMOVALS) or removals.target_people.nunique() != 1:
        raise ValueError("Expected one common target for the four candidate-removal scenarios")
    target = removals.target_people.iloc[0]
    if not ((removals.best_at_previous < target) & (removals.best_at_minimum >= target)).all():
        raise ValueError("A reported minimum does not bracket the target")
    walking = pd.read_csv(tables / "walking_thresholds.csv").query("service == 'meals'")
    minimum = walking.pivot(index="threshold_m", columns="menu", values="minimum_sites").sort_index()
    walk_settings = walking.groupby("threshold_m")[["speed_mps", "initial_unserved"]].first()

    fig = plt.figure(figsize=(6.55, 5.55))
    fig.text(.028, .974, "Reaching 5% of the initial meal-access gap", fontsize=14,
             weight="bold", va="top")
    fig.text(.028, .92, "Strict registry  ·  16 living circles  ·  15-minute walk", size=8.6, color=MUTED)
    heading(fig, .858, "A   Re-optimizing after individual restaurant removals")
    comparison = [Line2D([], [], marker="o", ms=4.8, lw=0, markeredgecolor=EXPANDED,
                         markerfacecolor="white", label="One fewer site"),
                  Line2D([], [], marker="o", ms=4.8, lw=0, color=EXPANDED,
                         label="Minimum feasible plan"),
                  Line2D([], [], linestyle=(0, (3, 2)), lw=1.2, color=MUTED,
                         label=f"5% target ({target:,.1f} residents)")]
    fig.legend(handles=comparison, ncol=3, frameon=False, loc="center left",
               bbox_to_anchor=(.024, .811), handlelength=1.4, columnspacing=1.4, fontsize=8.1)
    low = np.floor(removals.best_at_previous.min() / 100) * 100
    high = np.ceil(removals.best_at_minimum.max() / 100) * 100 + 70
    ax, trans = row_axis(fig, [.347, .551, .485, .192], removals, low, high)
    ax.axvline(target, color=MUTED, ls=(0, (3, 2)), lw=1.1)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.set_xlabel("Maximum number of newly reached residents", fontsize=8.3, labelpad=3)
    fig.text(.932, .754, "Min. sites", ha="center", color=MUTED, weight="bold", fontsize=8.3)
    for i, (key, row) in enumerate(removals.iterrows()):
        a, b = row.best_at_previous, row.best_at_minimum
        ax.plot([a, b], [i, i], color=EXPANDED, lw=1.3)
        ax.plot(a, i, "o", ms=4.8, mec=EXPANDED, mfc="white", mew=1.2)
        ax.plot(b, i, "o", ms=4.8, color=EXPANDED)
        ax.text(.028, i, REMOVALS[key], va="center", transform=trans, fontsize=8.3, linespacing=1.08)
        ax.text(.932, i, str(int(row.minimum_sites)), va="center", ha="center", transform=trans,
                fontsize=9.4, weight="bold")
    heading(fig, .416, "B   Minimum sites at three assumed walking speeds")
    legend = [Patch(facecolor=c, label=label) for label, c in MEAL_MENUS.values()]
    fig.legend(handles=legend, ncol=3, frameon=False, loc="center left", bbox_to_anchor=(.024, .369),
               fontsize=8.2, handlelength=1.05, columnspacing=1.3)
    ax = fig.add_axes([.103, .115, .858, .207])
    group = np.arange(len(minimum))
    width = .225
    for j, (key, (_, c)) in enumerate(MEAL_MENUS.items()):
        bars = ax.bar(group + (j - 1) * width, minimum[key], width=width * .90, color=c)
        ax.bar_label(bars, fmt="%.0f", padding=1.5, fontsize=8.4, color=INK)
    ax.set_ylim(0, minimum.max().max() + 1.6)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    ax.set_ylabel("Sites", fontsize=8.6)
    labels = [f"{walk_settings.loc[d, 'speed_mps']:.1f} m/s · {int(d)} m\n"
              f"{walk_settings.loc[d, 'initial_unserved']:,.0f} initially unserved" for d in minimum.index]
    ax.set_xticks(group, labels, fontsize=8.1)
    ax.tick_params(axis="both", length=0, pad=4, colors=MUTED)
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, lw=.6)
    ax.set_axisbelow(True)
    fig.text(.028, .017, "Panel A uses all screened candidates at 720 m. Each speed in panel B has "
             "its own baseline and target.", fontsize=8.05, color=MUTED)
    save_figure(fig, output, "FigS3_meal_site_and_walking_sensitivity")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, default=OUTPUT / "tables")
    parser.add_argument("--output", type=Path, default=OUTPUT / "figures")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": ["Arial", "DejaVu Sans"], "pdf.fonttype": 42,
                         "font.size": 8.6, "text.color": INK, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.unicode_minus": False})
    activity_figure(args.tables, args.output)
    meal_figure(args.tables, args.output)


if __name__ == "__main__":
    main()
