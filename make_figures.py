"""Role 3 figures + tables. Usage: python make_figures.py <path to results/modeling> [outdir]
Reads ONLY Role 2's saved CSVs; nothing is refitted.
Rebuilds figures 1–6 and tables from modeling outputs. Figure 7 (label coverage)
and the full match-rate table come from upstream data-section files and are not rebuilt here."""
import sys, pandas as pd, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
R = sys.argv[1].rstrip("/"); O = sys.argv[2] if len(sys.argv) > 2 else "."
import os; os.makedirs(f"{O}/figures", exist_ok=True); os.makedirs(f"{O}/tables", exist_ok=True)
L = ["hi","ta","te","ml","kn"]; NAME = dict(hi="Hindi",ta="Tamil",te="Telugu",ml="Malayalam",kn="Kannada")
plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False})

# Fig 1: directed transition-count matrix (source rows -> destination columns)
ps = pd.read_csv(f"{R}/preflight_dynamic/pair_screen.csv")
M = ps.pivot(index="source", columns="destination", values="events").reindex(index=L, columns=L)
fig, ax = plt.subplots(figsize=(5.5,4.5)); im = ax.imshow(M.values.astype(float), cmap="Blues")
ax.set_xticks(range(5), [NAME[l] for l in L], rotation=30); ax.set_yticks(range(5), [NAME[l] for l in L])
ax.set_xlabel("Destination (D)"); ax.set_ylabel("Source (S)"); ax.set_title("First-entry events by ordered pair (n=949)")
for i in range(5):
    for j in range(5):
        v = M.values[i,j]; ax.text(j,i,"–" if np.isnan(v) else int(v),ha="center",va="center",
                                   color="white" if v>80 else "black")
plt.colorbar(im,label="events"); plt.tight_layout(); plt.savefig(f"{O}/figures/fig1_transition_matrix.png",dpi=200); plt.close()
ps.to_csv(f"{O}/tables/table_pair_screen.csv", index=False)

# Fig 2: graph connectivity before/after
g = pd.read_csv(f"{R}/graph_audit_20261026/graph_connectivity_code_audit.csv")
lab = ["All titles\n(incl. unknown)","Clean resolved\ntitles only"]
fig, ax = plt.subplots(1,2,figsize=(8,3.8))
ax[0].bar(lab, g.components, color=["#2b6cb0","#c05621"]); ax[0].set_title("Connected components")
for i,v in enumerate(g.components): ax[0].text(i,v,f"{v:,}",ha="center",va="bottom")
ax[1].bar(lab, g.largest_component_share*100, color=["#2b6cb0","#c05621"]); ax[1].set_ylim(0,100)
ax[1].set_title("Actors in largest component (%)")
for i,v in enumerate(g.largest_component_share*100): ax[1].text(i,v,f"{v:.1f}%",ha="center",va="bottom")
plt.tight_layout(); plt.savefig(f"{O}/figures/fig2_graph_connectivity.png",dpi=200); plt.close()
g.to_csv(f"{O}/tables/table_graph_connectivity.csv", index=False)

# Fig 3: primary model comparison with bootstrap CIs
mc = pd.read_csv(f"{R}/final_v2/model_comparison.csv")
names = ["Career only","+ X→D","+ X→D + S→D"]
y = mc.test_AUPRC.values; lo = y-mc.test_AUPRC_CI_low.values; hi = mc.test_AUPRC_CI_high.values-y
fig, ax = plt.subplots(figsize=(5.5,3.8)); ax.bar(names,y,yerr=[lo,hi],capsize=5,color=["#a0aec0","#4299e1","#2b6cb0"])
ax.axhline(216/49291,ls="--",c="gray"); ax.text(2.4,216/49291,"base rate 0.0044",ha="right",va="bottom",fontsize=8)
ax.set_ylabel("Test AUPRC (2020–24)"); ax.set_title("Primary pooled model, 95% actor-bootstrap CI")
plt.tight_layout(); plt.savefig(f"{O}/figures/fig3_primary_auprc.png",dpi=200); plt.close()

# Fig 4: forest plot of S→D increment across analyses
s = pd.read_csv(f"{R}/modeling_summary.csv"); s = s[s.model=="career_plus_XD_SD"].copy()
s["an"] = s.analysis_directory.str.split("/").str[-1]
lbl = {"final_v2":"PRIMARY final_v2","strict_t1":"Strict Tier 1","unknown_counted":"Unknown in denominator",
       "two_year_exposure":"2-year exposure window","fixed_source":"Legacy fixed source",
       "dataset_final":"credits_final.parquet","dataset_baseline":"credits.parquet","ml_ta":"ml→ta only (39 test ev.)"}
order = list(lbl); s = s.set_index("an").loc[order].reset_index()
fig, ax = plt.subplots(figsize=(7,4))
yy = np.arange(len(s))[::-1]; c = s.delta_SD_increment_AUPRC
ax.errorbar(c, yy, xerr=[c-s.delta_SD_increment_AUPRC_CI_low, s.delta_SD_increment_AUPRC_CI_high-c], fmt="o", capsize=3, color="#2b6cb0")
ax.axvline(0,c="k",lw=.8); ax.set_yticks(yy,[lbl[a] for a in s.an]); ax.set_xlabel("Δ test AUPRC from adding S→D (95% CI)")
ax.set_title("S→D increments across analyses"); plt.tight_layout()
plt.savefig(f"{O}/figures/fig4_sensitivity_forest.png",dpi=200); plt.close()

# Fig 5: timing permutation null
rep = pd.read_csv(f"{R}/timing_permutation_20261026/timing_permutation_replicates.csv")
col = [c for c in rep.columns if "gain" in c.lower()][0]
obs = pd.read_csv(f"{R}/timing_permutation_20261026/timing_permutation.csv").observed_AUPRC_gain[0]
fig, ax = plt.subplots(figsize=(5.5,3.6)); ax.hist(rep[col],bins=40,color="#a0aec0")
ax.axvline(obs,c="#c05621",lw=2,label=f"observed = {obs:.4f}"); ax.legend()
ax.set_xlabel("Test AUPRC gain (Model 3 – Model 2)"); ax.set_title("Timing permutation null (1,000 reps, p = 0.000999)")
plt.tight_layout(); plt.savefig(f"{O}/figures/fig5_timing_permutation.png",dpi=200); plt.close()

# Fig 6: events by year with split boundaries
ev = pd.read_csv(f"{R}/preflight_dynamic/events_by_year.csv")
fig, ax = plt.subplots(figsize=(7,3.4)); ax.bar(ev.year, ev.first_entry_events, color="#4299e1")
for x,t in [(2016.5,"train | val"),(2019.5,"val | test")]: ax.axvline(x,ls="--",c="k"); ax.text(x,ax.get_ylim()[1]*.95,t,ha="right",fontsize=8)
ax.set_ylabel("First-entry events"); ax.set_title("Events per year and chronological split"); plt.tight_layout()
plt.savefig(f"{O}/figures/fig6_events_by_year.png",dpi=200); plt.close()

# Tables
keep = ["analysis_directory","dataset","model","test_AUPRC","test_ROC_AUC","test_Brier","test_events",
        "delta_XD_minus_career_AUPRC","delta_XD_minus_career_AUPRC_CI_low","delta_XD_minus_career_AUPRC_CI_high",
        "delta_SD_increment_AUPRC","delta_SD_increment_AUPRC_CI_low","delta_SD_increment_AUPRC_CI_high"]
pd.read_csv(f"{R}/modeling_summary.csv")[keep].round(5).to_csv(f"{O}/tables/table_all_analyses.csv",index=False)
print("done")
