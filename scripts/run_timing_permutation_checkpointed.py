import os
for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '1'
import json
import time
import hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from threadpoolctl import threadpool_limits
from cinema_mobility.modeling.pipeline import (
    CAREER, XD, SD, CATEGORICAL, _make_model, evaluate, permute_timing_features,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/modeling/timing_permutation_20261026'
PANEL_PATH = ROOT / 'results/modeling/actor_year_final_v2_dynamic.parquet'

def initialize():
    global PANEL, GROUPS, VALUES, FEATURES, FIT_MASK, TEST_MASK
    PANEL = pd.read_parquet(PANEL_PATH)
    assert len(PANEL) == 205664 and PANEL.event.sum() == 949
    GROUPS = [np.asarray(x) for x in PANEL.groupby(
        ['actor_id', 'source', 'destination', 'risk_spell_id'],
        sort=False, dropna=False).groups.values()]
    VALUES = PANEL[SD].to_numpy(copy=True)
    FEATURES = CAREER + XD + SD + CATEGORICAL
    FIT_MASK = PANEL.year.le(2019)
    TEST_MASK = PANEL.year.gt(2019)

def shuffled(seed):
    rng = np.random.default_rng(seed)
    values = VALUES.copy()
    for idx in GROUPS:
        values[idx] = VALUES[idx][rng.permutation(len(idx))]
    frame = PANEL[FEATURES].copy()
    frame[SD] = values
    return frame

def replicate(i):
    start = time.time()
    with threadpool_limits(limits=1):
        frame = shuffled(2026 + i + 1)
        model = _make_model(CAREER + XD + SD)
        model.fit(frame.loc[FIT_MASK], PANEL.loc[FIT_MASK, 'event'])
        pred = model.predict_proba(frame.loc[TEST_MASK])[:, 1]
        score = average_precision_score(PANEL.loc[TEST_MASK, 'event'], pred)
    return dict(repetition=i + 1, seed=2026 + i + 1,
                full_test_AUPRC=float(score), seconds=time.time()-start)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'timing_permutation.csv').exists():
        prior=json.loads((OUT/'validation.json').read_text())
        assert prior['panel_sha256']==hashlib.sha256(PANEL_PATH.read_bytes()).hexdigest()
        done=pd.read_csv(OUT/'timing_permutation_replicates.csv')
        assert len(done)==1000 and done.repetition.nunique()==1000
        print('This verified 1,000-repetition run is already complete; outputs preserved.',flush=True)
        return
    initialize()
    start = time.time()
    with threadpool_limits(limits=1):
        observed, _ = evaluate(PANEL, 2016, 2019, bootstrap_reps=0)
    scores = observed.set_index('model').test_AUPRC
    base = float(scores['career_plus_XD'])
    full = float(scores['career_plus_XD_SD'])
    saved = pd.read_csv(ROOT/'results/modeling/final_v2/model_comparison.csv').set_index('model')
    np.testing.assert_allclose(scores.values, saved.loc[scores.index,'test_AUPRC'].values,rtol=0,atol=1e-12)
    print('Observed primary scores reproduced; checking shuffle equivalence.',flush=True)
    reference = permute_timing_features(PANEL, 2027)
    np.testing.assert_array_equal(reference[SD].to_numpy(),shuffled(2027)[SD].to_numpy())
    with threadpool_limits(limits=1):
        reference_scores,_=evaluate(reference,2016,2019,bootstrap_reps=0)
    check = replicate(0)
    expected = float(reference_scores.set_index('model').loc['career_plus_XD_SD','test_AUPRC'])
    np.testing.assert_allclose(check['full_test_AUPRC'],expected,rtol=0,atol=1e-12)
    np.testing.assert_allclose(float(reference_scores.set_index('model').loc['career_plus_XD','test_AUPRC']),base,rtol=0,atol=1e-12)
    validation = dict(shuffle_exact_match=True, first_replicate_AUPRC_match=True,
                      invariant_M2_match=True, observed_scores_match=True,
                      panel_sha256=hashlib.sha256(PANEL_PATH.read_bytes()).hexdigest(),
                      panel_rows=len(PANEL),panel_events=int(PANEL.event.sum()),
                      repetitions_requested=1000,seed=2026,train_end_year=2016,
                      validation_end_year=2019,source_mode='dynamic',workers=4,
                      method='Existing saved primary panel; identical RNG/group order and model; invariant M2 cached; only M3 test fit repeated. Validation-only fits omitted because they do not enter test statistic.')
    if (OUT/'validation.json').exists():
        previous=json.loads((OUT/'validation.json').read_text())
        assert all(previous[k]==validation[k] for k in ['panel_sha256','repetitions_requested','seed','train_end_year','validation_end_year','source_mode'])
    (OUT/'validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    checkpoint=OUT/'replicates.jsonl'
    completed={}
    if checkpoint.exists():
        for line in checkpoint.read_text().splitlines():
            row=json.loads(line);completed[row['repetition']]=row
    print('Equivalence verified. Starting all 1,000 registered repetitions.',flush=True)
    with checkpoint.open('a',encoding='utf-8') as stream:
        if 1 not in completed:
            stream.write(json.dumps(check)+'\n');stream.flush();completed[1]=check
        with ProcessPoolExecutor(max_workers=4,initializer=initialize) as pool:
            futures=[pool.submit(replicate,i) for i in range(1000) if i+1 not in completed]
            for future in as_completed(futures):
                row=future.result();completed[row['repetition']]=row
                stream.write(json.dumps(row)+'\n');stream.flush()
                if len(completed)%25==0:
                    print(f'{len(completed)}/1000 complete; elapsed {time.time()-start:.0f}s',flush=True)
    ordered=pd.DataFrame([completed[i] for i in range(1,1001)])
    ordered['base_test_AUPRC']=base
    ordered['null_AUPRC_gain']=ordered.full_test_AUPRC-base
    assert len(ordered)==1000 and ordered.repetition.nunique()==1000
    null=ordered.null_AUPRC_gain.to_numpy();gain=full-base
    result=pd.DataFrame([dict(source='all',destination='all',observed_AUPRC_gain=gain,
        permutation_p_value=(1+int((null>=gain).sum()))/1001,permutations=1000,
        null_mean_gain=float(np.mean(null)),null_95pct=float(np.quantile(null,.95)),seed=2026)])
    ordered.to_csv(OUT/'timing_permutation_replicates.csv',index=False)
    result.to_csv(OUT/'timing_permutation.csv',index=False)
    print(result.to_string(index=False),flush=True)
    print('COMPLETE',flush=True)

if __name__=='__main__':
    main()
