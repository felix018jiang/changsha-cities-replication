"""Plot exact pooled objective values from auditable budget tables."""
import csv
import math
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR",str(Path(tempfile.gettempdir())/"changsha-figure-cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, PercentFormatter
from matplotlib.lines import Line2D
from matplotlib import font_manager

ROOT=Path(__file__).resolve().parents[3]
ROUND=ROOT/"outputs/长沙养老研究_第十九轮"
TABLES=ROUND/"tables"


def read(name):
    with (TABLES/name).open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))


def main():
    rows=[r for r in read("siting_budget_comparison.csv") if r["scope"]=="ALL16"]
    limits={(r["registry"],r["function"],r["menu"]):r for r in read("siting_ceiling.csv") if r["scope"]=="ALL16"}
    fname="Arial" if any(f.name=="Arial" for f in font_manager.fontManager.ttflist) else "DejaVu Sans"
    plt.rcParams.update({"font.family":fname, "axes.spines.top":False, "axes.spines.right":False,
                         "svg.fonttype":"none","pdf.fonttype":42,"font.size":9.5})
    fig, axes=plt.subplots(1,2,figsize=(8,4.25))
    fig.subplots_adjust(left=.092, right=.975, top=.69, bottom=.255, wspace=.27)
    fg="#1c343c";secondary="#506067"
    colors={"inside_only":"#2e5e7a","expanded":"#aa5129"}
    ymax=max(float(r["attainable_percent"]) for r in limits.values())
    ystep=5 if ymax<=30 else 10
    for i,(ax, f, service) in enumerate(zip(axes,("M","S"),("Meal service","Daytime activities"))):
        xmax=max(int(limits[g,f,m]["minimum_sites_to_saturate"]) for g in ("strict","wide") for m in colors)
        xmax=max(1,xmax)
        for menu in colors:
            for registry,style in (("strict","-"),("wide",(0,(1.2,1.6)))):
                dd=sorted([r for r in rows if r["function"]==f and r["registry"]==registry
                           and int(r["site_budget"])<=min(xmax,int(r[menu+"_eligible"]))],key=lambda r:int(r["site_budget"]))
                # One point per feasible integer budget. Both registries share the same legend and axes.
                xx=[int(r["site_budget"]) for r in dd]
                yy=[float(r[menu+"_gain_pct"]) for r in dd]
                ax.plot(xx,yy,color=colors[menu],ls=style,lw=2 if registry=="strict" else 1.5,alpha=1,
                        marker="o" if registry=="strict" else None,ms=2.6,clip_on=False,zorder=3)
        strict=limits["strict",f,"expanded"];wide=limits["wide",f,"expanded"]
        ax.set_title(f"{'AB'[i]}   {service}",loc="left",color=fg,fontsize=12,fontweight="bold",pad=10)
        ax.set_xlim(0,xmax);ax.xaxis.set_major_locator(MaxNLocator(nbins=6,integer=True))
        ax.set_ylim(0, max(ystep,math.ceil(ymax/ystep)*ystep));ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=0))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.set_xlabel("Additional sites",color=fg,labelpad=6)
        ax.tick_params(axis="both",colors=secondary,length=3,width=.6)
        ax.tick_params(axis="y",length=0);ax.grid(axis="y",lw=.65,color="#d9e0e2");ax.set_axisbelow(True)
        for spine in ("left","bottom"):ax.spines[spine].set_color("#b1bec0")
        ns=float(strict["initial_uncovered"]);nw=float(wide["initial_uncovered"])
        ax.text(0,-.32,f"Initially unserved, strict / wider: {ns:,.0f} / {nw:,.0f}",transform=ax.transAxes,
                color=secondary,fontsize=8,va="top",ha="left")
    fig.text(.039,.935,"New 15-minute walking access at each site budget",fontsize=15.5,fontweight="bold",color=fg)
    fig.text(.039,.875,"16 circles jointly optimized  ·  Share of previously unserved residents aged 60+ newly covered",
             fontsize=9.4,color=secondary)
    handles=[Line2D([],[],color=colors["inside_only"],lw=2.2,label="Existing station sites only"),
             Line2D([],[],color=colors["expanded"],lw=2.2,label="Station and off-site locations"),
             Line2D([],[],color=fg,lw=1.8,ls="-",label="Strict registry"),
             Line2D([],[],color=fg,lw=1.8,ls=(0,(1.2,1.6)),label="Wider registry")]
    fig.legend(handles=handles,ncol=4,frameon=False,loc="upper left",bbox_to_anchor=(.035,.851),
               fontsize=8.3,handlelength=1.8,columnspacing=1.6)
    fig.text(.039,.036,"Each marker represents a feasible site count. A plateau means that additional sites reach no further unserved residents.",
             fontsize=8,color=secondary)
    out=ROUND/"figures";out.mkdir(parents=True,exist_ok=True)
    for fmt in ["png","svg"]:
        fig.savefig(out/f"budget_frontiers.{fmt}",dpi=240,facecolor="white")
    plt.close(fig)


if __name__=="__main__":main()
