"""Optional browser DOM exercise with mocked HTTP; NOT a live HTTP E2E test."""
from __future__ import annotations
import json
import os
import shutil
import pytest
if os.getenv('DBOUND_BROWSER_TEST') != '1':
    pytest.skip('requires opt-in browser runner', allow_module_level=True)
playwright = pytest.importorskip('playwright.sync_api')
from fastapi.testclient import TestClient
from decisionbound.web import create_app

from .test_review_gate_sprint08 import completed


def test_real_chromium_review_form_with_mocked_http(completed):
    _, _, _, wid = completed
    client = TestClient(create_app())
    html = client.get('/').text
    from decisionbound.review_gate import ReviewGate
    from decisionbound.settings import repository, state_dir
    meta = ReviewGate(repository(), state_dir()).snapshot(wid, include_diff=False)
    preview = ReviewGate(repository(), state_dir()).snapshot(wid, include_diff=True)
    with playwright.sync_playwright() as pw:
        chromium = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = pw.chromium.launch(headless=True, executable_path=chromium,
                                      args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = browser.new_page()
            errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            # This never uses the network; TestClient tests the real API in a
            # separate suite. Here we test actual browser DOM and inline JS.
            js = '''window.__calls=[];const meta=__META__,preview=__PREVIEW__;
                window.fetch=async (url,opts={})=>{
                  window.__calls.push({url:String(url),body:opts.body||null});
                  if(String(url).includes('include_diff=true'))
                    return {ok:true,json:async()=>preview};
                  if(String(url).endsWith('approve_export')) {
                    return {ok:true,json:async()=>({decision:'approve_export',revision:1})}; }
                  if(String(url).includes('/review')) {
                    if(window.__calls.some(c=>c.url.endsWith('approve_export')))
                      return {ok:true,json:async()=>({...meta,approval:{revision:1,decision:'approve_export',fingerprint:meta.fingerprint}})};
                    return {ok:true,json:async()=>meta};
                  }
                  return {ok:false,json:async()=>({detail:'unknown mock route'})};
                };'''.replace('__META__',json.dumps(meta)).replace('__PREVIEW__',json.dumps(preview))
            page.evaluate(js)
            page.set_content(html, wait_until='load')
            page.locator('#token').fill('fake-token-only-dom')
            page.locator('#workflow').fill(wid)
            page.locator('#review-load').click()
            page.get_by_text('Review metadata loaded.').wait_for(timeout=3000)
            assert 'VALUE = 2' not in page.locator('#review-metadata').inner_text()
            page.locator('#review-approve').click()
            page.get_by_text('Action refused: Preview the patch').wait_for()
            page.locator('#review-preview').click()
            assert '+VALUE = 2' in page.locator('#review-diff').inner_text()
            page.locator('#review-confirm').check()
            page.locator('#review-approve').click()
            page.get_by_text('Explicit acknowledgment of unverified tests is required').wait_for()
            page.locator('#review-unverified').check()
            page.locator('#review-approve').click()
            page.get_by_text('Recorded approve_export at revision 1').wait_for()
            calls = page.evaluate('window.__calls')
            assert any('include_diff=true' in call['url'] for call in calls)
            approved = next(call for call in calls if call['url'].endswith('approve_export'))
            assert json.loads(approved['body'])['confirm_export_only'] is True
            assert json.loads(approved['body'])['acknowledge_unverified'] is True
            assert not errors,errors
        finally:
            browser.close()
