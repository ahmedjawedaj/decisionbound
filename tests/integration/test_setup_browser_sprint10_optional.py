"""Opt-in real Chromium DOM behavior, with fake HTTP (not true E2E)."""
from __future__ import annotations
import json
import os
import shutil
import pytest
if os.getenv('DBOUND_BROWSER_TEST') != '1':
    pytest.skip('Requires explicit Chromium DOM runner',allow_module_level=True)
playwright = pytest.importorskip('playwright.sync_api')
from fastapi.testclient import TestClient
from decisionbound.web import create_app


def test_profile_switch_and_no_script_injection(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME',str(tmp_path/'state'))
    html=TestClient(create_app()).get('/').text
    with playwright.sync_playwright() as pw:
        binary=shutil.which('chromium') or shutil.which('chromium-browser')
        browser=pw.chromium.launch(headless=True,executable_path=binary,
                                   args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page=browser.new_page()
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            fake='''window.fetch=async function(url,opts){
              if(String(url).includes('/api/profiles/openrouter/activate'))return {ok:true,json:async()=>({})};
              if(String(url).includes('/api/profiles'))return {ok:true,json:async()=>({
                active:window.activated?'openrouter':'local-safe',profiles:['local-safe','openrouter']})};
              if(String(url).includes('/api/setup/v1/status'))return {ok:true,json:async()=>({
                profile:'openrouter',ready:false,checks:[],
                next_action:'<img src=x onerror=alert(1)>',integration_tested:false})};
              return {ok:false,status:404,json:async()=>({detail:'Missing'})};
            };const originalFetch=window.fetch;
            window.fetch=(url,opts)=>{if(String(url).includes('/activate'))window.activated=true;
              return originalFetch(url,opts);};'''
            page.evaluate(fake)
            page.set_content(html,wait_until='load')
            page.locator('#token').fill('dummy-local-token')
            page.locator('#setup-load').click()
            page.locator('#setup-profile option').first.wait_for(state='attached',timeout=3000)
            page.locator('#setup-profile').select_option('openrouter')
            page.locator('#setup-activate').click()
            page.get_by_text('Prerequisites: incomplete').wait_for(timeout=3000)
            assert page.locator('#setup-status img').count()==0
            assert '<img src=x' in page.locator('#setup-status').inner_text()
            assert not errors, errors
        finally:browser.close()
