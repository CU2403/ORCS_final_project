# app.py

from pathlib import Path
from collections import OrderedDict

import pandas as pd
from flask import (
    Flask, render_template, request,
    jsonify, url_for, session, redirect
)
# near the top of app.py
from datetime import datetime



# -----------------------------------------------------------------------------
# Configuration & data load
# -----------------------------------------------------------------------------
DATA_FILE = Path(__file__).parent / "privacy.xlsx"
df = pd.read_excel(DATA_FILE)

deal_col = next(
    (c for c in df.columns if "deal" in c.lower()), 
    None
)

deal_map: dict[str, dict[str, list[str]]] = {}
if deal_col:
    for _, row in df.iterrows():
        q = row["Question"].strip()
        a = str(row["Answer Option"]).strip()
        raw = row.get(deal_col, "")             # e.g. "MPC; Differential Privacy"
        if pd.isna(raw) or not raw:
            continue
        pets = [p.strip() for p in str(raw).split(";") if p.strip()]
        if pets:
            deal_map.setdefault(q, {})[a] = pets


# 1) Normalize question text (strip whitespace) so duplicates unify
df["Question"] = df["Question"].astype(str).str.strip()

# 2) Detect if a Parameter Suggestions column exists
param_col = next(
    (c for c in df.columns if c.lower().startswith("parameter suggestion")), 
    None
)

# 3) Build fast lookup { question_text -> { answer_option -> {techs, params} } }
lookup: dict[str, dict[str, dict]] = {}
for _, row in df.iterrows():
    q = row["Question"]
    a = str(row["Answer Option"]).strip()

    # Recommended Techniques → list of strings (safe split)
    raw_techs = row.get("Recommended Techniques", "")
    raw_techs = "" if pd.isna(raw_techs) else str(raw_techs)
    techs = [t.strip() for t in raw_techs.split(";") if t.strip()]

    # Parameter Suggestions → string or empty
    params = ""
    if param_col:
        raw_params = row.get(param_col, "")
        params = "" if pd.isna(raw_params) else str(raw_params)

    lookup.setdefault(q, {})[a] = {
        "techs": techs,
        "params": params
    }


# -----------------------------------------------------------------------------
# Build screening questions
# -----------------------------------------------------------------------------

def build_questions():
    q_order = list(OrderedDict.fromkeys(df["Question"].tolist()))
    grouped = df.groupby("Question")["Answer Option"].apply(list)

    questions = []
    for idx, q_text in enumerate(q_order, start=1):
        question = {
            "id":      f"q{idx}",
            "text":    q_text,
            "multi":   "select all" in q_text.lower() or "kind of data" in q_text.lower(),
            "options": grouped[q_text]
        }
        # **only** Q4 depends on Q3=Real-time/interactive
        if "If real-time or interactive results are needed" in q_text:
            question["depends_on"]    = "q3"
            question["depends_value"] = "Real-time/interactive"

        questions.append(question)

    return questions



# -----------------------------------------------------------------------------
# Score screening answers
# -----------------------------------------------------------------------------
def evaluate(answers: dict):
    votes = {}
    params_out = []
    vetoed = set()

    # 1) Tally votes & collect params as before
    for q_text, sel in answers.items():
        sels = sel if isinstance(sel, list) else [sel]
        for ans in sels:
            entry = lookup.get(q_text, {}).get(ans)
            if not entry: 
                continue
            for tech in entry["techs"]:
                votes[tech] = votes.get(tech, 0) + 1
            if entry["params"]:
                params_out.append(entry["params"])

    # 2) Apply any deal-breakers
    #    Any PET listed under deal_map[q_text][ans] is vetoed
    for q_text, sel in answers.items():
        if not deal_col:
            break
        sels = sel if isinstance(sel, list) else [sel]
        for ans in sels:
            pets_to_veto = deal_map.get(q_text, {}).get(ans, [])
            for pet in pets_to_veto:
                vetoed.add(pet)

    # 3) Filter out vetoed PETs entirely
    for pet in vetoed:
        votes.pop(pet, None)

    # 4) Build the final ranked list
    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    ranked_pets = [
        {"name": tech, "score": cnt, "rationale": ("VETOED" if tech in vetoed else "Matches survey")}
        for tech, cnt in ranked
    ]

    # 5) Deduplicate parameter suggestions
    param_suggestions = sorted(set(params_out))

    # 6) Prepare a summary of which PETs were vetoed and why
    veto_summary = []
    for pet in vetoed:
        # find all (q,ans) that vetoed this pet
        reasons = []
        for q_text, sel in answers.items():
            sels = sel if isinstance(sel, list) else [sel]
            for ans in sels:
                if pet in deal_map.get(q_text, {}).get(ans, []):
                    reasons.append(f"{q_text} → {ans}")
        veto_summary.append({"name": pet, "reasons": reasons})

    return ranked_pets, param_suggestions, veto_summary



# -----------------------------------------------------------------------------
# Implementation-wizard sub-questions
# -----------------------------------------------------------------------------
def dp_steps():
    return [
        {
            "id": "D1",
            "text": "1) Maximum absolute error you can tolerate (Δ=1):",
            "input_type": "number",
            "placeholder": "e.g. 2.0"
        },
        {
            "id": "D2",
            "text": "2) Expected number of queries per day:",
            "input_type": "number",
            "placeholder": "e.g. 50"
        }
    ]

def mpc_steps():
    return [
            {
                "id": "S1",
                "text": "1) How many parties are involved?",
                "input_type": "number",
                "placeholder": "e.g. 3"
            },
            {
                "id": "S2",
                "text": "2) How many parties do you think might collude or be taken over by an attacker?",
                "input_type": "number",
                "placeholder": "e.g. 1"
            }
        ]

def sd_steps():
    return [
        {"id":"S1",
         "text":"1) What kind of data are you synthesizing?",
         "options":["Tabular","Time-series","Graph","Images / Unstructured"]},
        {"id":"S2",
         "text":"2) Desired synthetic dataset size:",
         "options":["Same as real data","Smaller (e.g. 50%)","Larger (e.g. 200%)","Custom…"]},

        {"id":"S4",
         "text":"4) Do you want Differential Privacy on the synthetic generator?",
         "options":["Yes (DP-GAN)","No"]},
        {"id":"S5",
         "text":"5) How often regenerate synthetic data?",
         "options":["One-time snapshot","Daily","Weekly","Monthly","Custom…"]},
        {"id":"S6",
         "text":"6) Which evaluation criteria matter most?",
         "options":["Statistical similarity (KS, Chi-square)",
                  "ML model performance (accuracy, F1)",
                  "Privacy risk metrics (membership inference, MI)",
                  "User feedback / qualitative testing"]},
        {"id":"S7",
         "text":"7) What are your computational and hardware constraints for generating synthetic data?",
         "options":[
           "High-performance GPUs/TPUs in the cloud",
           "On-premises CPU servers only",
           "Trusted hardware enclaves (TEE) available",
           "Very limited compute budget (e.g. single CPU)"
         ]}
    ]

def ka_steps():
    return [
        {
            "id": "K1",
            "text": "1) Approximately how many unique records does your dataset contain?",
            "options": ["<10k", "10k–100k", "100k–1M", ">1M"]
        },
        {
            "id": "K2",
            "text": "2) What maximum re-identification risk do you accept?",
            "options": ["Very low (<1%)", "Low (1–5%)", "Moderate (5–10%)"]
        }
    ]

def te_steps():
    return [
        {
            "id": "T1",
            "text": "1) Approximately how many unique records will you process?",
            "options": ["<100k", "100k–1M", ">1M"]
        },
        {
            "id": "T2",
            "text": "2) Do you have secure hardware enclaves available?",
            "options": ["Intel SGX / AMD SEV", "AWS Nitro Enclaves", "No"]
        }
    ]
# -----------------------------------------------------------------------------
# Flask app & routes
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = "CHANGE_ME_IN_PROD"

@app.context_processor
def inject_globals():
    return {"current_year": datetime.utcnow().year}

@app.get("/")
def index():
    # Pass the cleaned questions list into the template
    return render_template("index.html", questions=build_questions())


@app.post("/results")
def results_api():
    raw = request.get_json(force=True)  # whatever shape it is in
    # build a mapping id→text so we can translate id-keys back to text
    questions = build_questions()
    id_to_text = {q["id"]: q["text"] for q in questions}

    # build the final answers dict: { question_text: answer }
    answers_by_text = {}
    for key, val in raw.items():
        if key in id_to_text:
            # key was "q1","q2",… → translate
            answers_by_text[id_to_text[key]] = val
        else:
            # assume key already *is* the question text
            answers_by_text[key] = val

    # now evaluate against your lookup
    ranked, params, veto = evaluate(answers_by_text)

    session["ranked"] = ranked
    session["params"] = params
    session["vetoed"] = veto

    tools = ",".join(r["name"] for r in ranked)
    return jsonify({"redirect": url_for("show_results", tools=tools)})



@app.get("/results")
def show_results():
    ranked = session.get("ranked", [])
    params = session.get("params", [])
    vetoed = session.get("vetoed", [])
    all_tools = request.args.get("tools", "").split(",")
    top3_tools = all_tools
    session["wizard_tools"] = top3_tools[:3]

    # Split ranked into privacy techniques vs policy recommendations
    recommended_privacy_techniques = []
    recommended_policies = []

    for item in ranked:
        name = item.get("name", "").lower()
        if any(keyword in name for keyword in ["compliance", "policy", "regulation", "ferpa", "hipaa", "gdpr", "foia"]):
            recommended_policies.append(item)
        else:
            recommended_privacy_techniques.append(item)

    # Only take top 2 from each
    top_privacy_techniques = recommended_privacy_techniques[:2]
    top_policies = recommended_policies

    return render_template(
        "results.html",
        privacy_techniques=top_privacy_techniques,
        policies=top_policies,
        parameters=params,
        vetoed=vetoed,
        wizard_tools=top3_tools
    )



@app.get("/wizard")
def wizard():
    tool_raw = request.args.get("tool", "").lower().strip()

    # Differential Privacy
    if "differential privacy" in tool_raw:
        questions = dp_steps()
        display   = "Differential Privacy"

    # Secure MPC (catch hyphens, spaces or "mpc")
    elif any(kw in tool_raw for kw in ("multiparty", "multi-party", "mpc")):
        questions = mpc_steps()
        display   = "Secure Multiparty Computation"

    # Synthetic Data
    elif "synthetic data" in tool_raw:
        questions = sd_steps()
        display   = "Synthetic Data Generation"

    # Trusted Execution Environments
    elif "trusted execution" in tool_raw or "tee" in tool_raw:
        questions = te_steps()
        display   = "Trusted Execution Environments"

    # k-anonymity & ℓ-diversity
    elif "k-anonymity" in tool_raw or "l-diversity" in tool_raw:
        questions = ka_steps()
        display   = "k-anonymity & ℓ-diversity"

    else:
        # no match → error
        return "This tool is hard to give advice on implementation simply in a wizard. Please consider gain advice from a data privacy expert.", 400

    return render_template(
        "wizard.html",
        steps=[{"tool": display, "questions": questions}],
        selected_tool=display
    )





@app.post("/wizard/submit")
def wizard_submit():
    data = request.get_json(force=True)
    tool = data.pop("tool", None) or ""
    session["last_tool"] = tool
    config = []
    
    if tool == "Differential Privacy":
        # DP logic
        try:
            err = float(data.get("D1", 0))
            qpd = int(data.get("D2", 0))
            eps_q = 1.0/err if err>0 else 0.0
            eps_tot = eps_q * qpd
            config.append(f"ε per query ≈ {eps_q:.3f}")
            config.append(f"Total ε/day ≈ {eps_tot:.3f}")
        except Exception:
            config.append("⛔ Invalid DP inputs—could not compute ε.")

    elif tool == "Synthetic Data Generation":
        # Start building the config output
        config.append("Your choices for Synthetic Data Generation:")
        
        # Map step IDs to human-readable question labels
        step_labels = {
            "S1": "Data type",
            "S2": "Dataset size",
            "S3": "Generation method",
            "S4": "Differential Privacy",
            "S5": "Regeneration frequency",
            "S6": "Evaluation criteria",
            "S7": "Hardware constraints"
        }
        # Echo back each answer
        for step in ["S1","S2","S3","S4","S5","S6","S7"]:
            if step in data:
                label = step_labels.get(step, step)
                config.append(f"  • {label}: {data[step]}")
        
        config.append("")  # blank line
        config.append("Implementation tips and recommendations:")

        # S1 → method category
        dtype = data.get("S1","").lower()
        if "tabular" in dtype:
            config.append("• For tabular data, consider CTGAN or TVAE implementations.")
        elif "time-series" in dtype:
            config.append("• For time-series, look at TimeGAN or DP-TS synth frameworks.")
        elif "graph" in dtype:
            config.append("• For graph data, explore GraphGAN or PrivGraph.")
        elif "images" in dtype or "unstructured" in dtype:
            config.append("• For images/unstructured, try DP-GAN or PATE-GAN variants.")
        
        # S2 → relative sizing
        size = data.get("S2","").lower()
        if "smaller" in size:
            config.append("• Smaller samples → faster training; verify distribution overlap.")
        elif "larger" in size:
            config.append("• Larger synthetic sets may amplify biases—monitor quality metrics.")
        elif "custom" in size:
            config.append("• Custom sizes: balance compute cost vs. data utility.")

        # S3 → specific method
        method = data.get("S3","").lower()
        if "gan" in method:
            config.append("• GAN-based → CTGAN/TVAE for tabular, StyleGAN for images.")
        elif "bayesian" in method or "copula" in method:
            config.append("• Bayesian/Copula → SDV’s Bayesian network or CopulaGAN.")
        elif "vae" in method:
            config.append("• VAE-based → try DP-VAE or VAE-GAN hybrids.")
        
        # S4 → differential privacy toggle
        dp_on = data.get("S4","").lower()
        if "yes" in dp_on:
            config.append("• DP enabled → tune ε carefully; too small harms utility.")
        else:
            config.append("• No DP → ensure data leakage risk is acceptable.")

        # S5 → regeneration schedule
        regen = data.get("S5","").lower()
        if "daily" in regen:
            config.append("• Daily regen → automate retraining & quality checks.")
        elif "weekly" in regen:
            config.append("• Weekly regen → balance freshness vs. compute cost.")
        elif "monthly" in regen:
            config.append("• Monthly regen → schedule performance benchmarks.")
        elif "custom" in regen:
            config.append("• Custom regen → integrate with your CI/CD pipeline.")

        # S6 → evaluation metrics
        eval_crit = data.get("S6","").lower()
        if "statistical" in eval_crit:
            config.append("• Use KS test, Chi-square for marginal distribution checks.")
        if "ml model" in eval_crit:
            config.append("• Train downstream models and compare accuracy/F1.")
        if "privacy risk" in eval_crit:
            config.append("• Run membership inference and attribute inference attacks.")
        if "user feedback" in eval_crit:
            config.append("• Collect domain expert feedback on synthetic realism.")

        # S7 → hardware recommendations
        hw = data.get("S7","").lower()
        if "gpu" in hw or "tpu" in hw:
            config.append("• Leverage cloud GPUs/TPUs for faster model convergence.")
        elif "cpu" in hw:
            config.append("• CPU only → use lightweight, non-neural methods (Copula, Bayesian).")
        elif "tee" in hw:
            config.append("• TEE available → run sensitive data synth inside enclaves.")
        
        # Final fallback tip
        config.append("")
        config.append("For more, see: SDV (sdv.dev), CTGAN docs, or your preferred synth library.")


    elif tool == "Secure Multiparty Computation":
    # MPC logic
        try:
            # Number of parties and maximum corruptions
            n = int(data.get("S1", 0))
            t = int(data.get("S2", 0))
        except ValueError:
            n, t = None, None

        config.append("Your MPC configuration:")
        if n is None or t is None:
            config.append("  ⛔ Invalid inputs—please enter integers for parties and threshold.")
        else:
            config.append(f"  • Total parties (n): {n}")
            config.append(f"  • Max corruptions tolerated (t): {t}")
            config.append("")
            config.append("Protocol recommendations:")

        # Choose protocol family by adversary tolerance
        # Semi-honest BGW/GMW if t < n/2; malicious SPDZ if t < n
        if  t < n/2:
            config.append("  – Semi-honest model (t < n/2): Consider BGW or GMW (Shamir secret‐sharing).")
        elif t < n:
            config.append("  – Malicious model (t < n): Consider SPDZ/MASCOT or HoneyBadgerMPC for stronger security.")
        else:
            config.append("  – Warning: t must be < n for security—please adjust your threshold.")
            config.append("")
            config.append("Libraries & frameworks:")
            config.append("  • MP-SPDZ (C++): supports many protocols & security levels.")
            config.append("  • SCALE-MAMBA (Python/C++): friendly DSL & semi-honest/SPDZ.")
            config.append("  • VIFF (Python): easy prototyping, semi-honest only.")
            config.append("  • Sharemind (Rust): commercial‐grade with high‐performance optimizations.")

        config.append("")
        config.append("Tip: choose your n-of-t threshold based on your trust & threat model.")

    
    elif tool == "Trusted Execution Environments":
        # TEE logic
        config.append("Your TEE settings:")
        t1 = data.get("T1", "")
        t2 = data.get("T2", "")
        config.append(f"  Records: {t1}")
        config.append(f"  Enclave available: {t2}")
        config.append("")
        config.append("Implementation tips:")
        if "Intel" in t2:
            config.append(
              "• Deploy your code in Intel SGX/AMD SEV enclaves. "
              f"For datasets {t1}, use chunked loading to stay within enclave memory limits."
            )
        elif "Nitro" in t2:
            config.append(
              "• Use AWS Nitro Enclaves with KMS attestation—follow AWS Nitro CLI docs."
            )
        else:
            config.append(
              "• No hardware enclaves available. Consider Azure Confidential VMs "
              "or fallback to MPC for compute isolation."
            )

    elif tool == "k-anonymity & ℓ-diversity":
        # k-anonymity logic
        config.append("Your anonymization settings:")
        k1 = data.get("K1", "")
        k2 = data.get("K2", "")
        config.append(f"  Dataset size: {k1}")
        config.append(f"  Risk tolerance: {k2}")

        # map to k-value
        size_map = {
            "<10k":      {"Very low (<1%)": 5,   "Low (1–5%)": 10,  "Moderate (5–10%)": 20},
            "10k–100k":  {"Very low (<1%)": 10,  "Low (1–5%)": 20,  "Moderate (5–10%)": 50},
            "100k–1M":   {"Very low (<1%)": 20,  "Low (1–5%)": 50,  "Moderate (5–10%)": 100},
            ">1M":       {"Very low (<1%)": 50,  "Low (1–5%)": 100, "Moderate (5–10%)": 200}
        }
        k_val = size_map.get(k1, {}).get(k2)
        l_map = {"Very low (<1%)": 2, "Low (1–5%)": 3, "Moderate (5–10%)": 5}
        l_val = l_map.get(k2, 2)

        config.append("")
        if k_val:
            config.append(f"• Generalize/suppress to achieve k={k_val} and ℓ={l_val}.")
            config.append("  – k (anonymity): each record is indistinguishable from at least k–1 others sharing the same quasi-identifiers.")
            config.append("  – ℓ (diversity): each such group must contain at least ℓ distinct sensitive-attribute values.")
            config.append("  Use a library like ARX (Java) or sdcMicro (R/Python).")
        else:
            config.append("• Unable to derive k/ℓ for those choices—please adjust settings.")


    else:
        config.append(f"No wizard logic found for tool: {tool}")

    session["config"] = config
    return jsonify({"redirect": url_for("wizard_results")})





@app.get("/wizard/results")
def wizard_results():
    cfg         = session.get("config", [])
    all_tools   = session.get("wizard_tools", [])
    current     = session.get("last_tool", "")

    # drop any that look like policies/compliance
    policy_keywords = (
      "compliance", "policy", "regulation",
      "ferpa", "hipaa", "gdpr", "foia", "ccpa", "coppa"
    )
    privacy_tools = [
      t for t in all_tools
      if not any(kw in t.lower() for kw in policy_keywords)
    ]

    return render_template(
        "wizard_results.html",
        config=cfg,
        wizard_tools=privacy_tools,
        current_tool=current
    )



if __name__ == "__main__":
    app.run(debug=True)
