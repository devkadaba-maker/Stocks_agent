"""Verify guard behavior explicitly, including checks skipped by an existing freeze."""
from pathlib import Path
import tempfile,json,contextlib,io
import numpy as np
import pandas as pd
import engine, final_test, search
from independent_audit import averages, PROOF

def main():
    checks={}
    n=500;cut=320
    c=100+np.sin(np.arange(n)/17)*5+np.arange(n)*.03
    h=c+2;l=c-2
    original=averages(c,h,l,10,50)
    changed_c=c.copy();changed_h=h.copy();changed_l=l.copy()
    changed_c[cut:]*=8;changed_h[cut:]*=8;changed_l[cut:]*=8
    changed=averages(changed_c,changed_h,changed_l,10,50)
    for a,b in zip(original,changed):np.testing.assert_array_equal(a[:cut],b[:cut])
    checks['independent_ema_and_atr_future_shock']='passed'
    root=engine.ROOT
    with tempfile.TemporaryDirectory() as temporary:
        engine.ROOT=Path(temporary)
        try:
            try:engine.load('BTCUSDT','sealed','2019-12')
            except RuntimeError as err:
                assert 'sealed' in str(err).lower()
                checks['sealed_loader_without_selection']='passed'
            else:raise AssertionError('Seal failed')
        finally:engine.ROOT=root
    for name,call in [('final_assessment_reentry_block',final_test.main),('training_after_selection_block',search.train),('validation_after_selection_block',search.validate)]:
        try:call()
        except RuntimeError:checks[name]='passed'
        else:raise AssertionError(name)
    # Original family-wide causality and order-edge cases, with its existing
    # conditional seal check supplemented by the explicit empty-directory test above.
    import verify
    capture=io.StringIO()
    with contextlib.redirect_stdout(capture):verify.verify()
    checks['original_family_causality_and_order_edge_cases']='passed'
    checks['scope']='synthetic invariance and state guards; not proof of Pine realtime behavior or future profits'
    (PROOF/'guard_checks.json').write_text(json.dumps(checks,indent=2))
    print(json.dumps(checks,indent=2))

if __name__=='__main__':main()
