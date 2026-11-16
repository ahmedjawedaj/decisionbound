"""Optional real Chromium DOM test using mocked API, not HTTP end-to-end."""
from __future__ import annotations
import json
import os
import shutil
import pytest

if os.getenv('DBOUND_BROWSER_TEST') != '1':
    pytest.skip('Browser DOM requires explicit DBOUND_BROWSER_TEST=1', allow_module_level=True)
playwright = pytest.importorskip('playwright.sync_api')
from fastapi.testclient import TestClient
from decisionbound.web import create_app

PROJECT = 'prj_' + 'a' * 32
SESSION = 'wf_' + 'b' * 32


def test_sessions_panel_incremental_feed_and_no_unsafe_html(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path/'private'))
    html = TestClient(create_app()).get('/').text
    with playwright.sync_playwright() as pw:
        binary = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = pw.chromium.launch(headless=True, executable_path=binary,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = browser.new_page()
            errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            js = '''window.__urls=[];const pr=__PROJECT__,wf=__SESSION__;
            window.fetch=async function(url){window.__urls.push(String(url));
              if(String(url).includes('/sessions?')) return {ok:true,json:async()=>({sessions:[
                {session_id:wf,state:'<img src=x onerror=alert(1)>',task_count:2}]})};
              if(String(url).includes('/changes?')) return {ok:true,json:async()=>({
                events:[{id:3,session_id:wf,event:'created'}],next_after:3,has_more:false})};
              if(String(url).includes('/events')) return {ok:true,json:async()=>[]};
              if(String(url).includes('/workflows/')) return {ok:true,json:async()=>({state:'running',tasks:[]})};
              return {ok:false,json:async()=>({detail:'unknown route'})};};'''
            page.evaluate(js.replace('__PROJECT__',json.dumps(PROJECT)).replace('__SESSION__',json.dumps(SESSION)))
            page.set_content(html, wait_until='load')
            page.locator('#token').fill('not-a-real-token')
            page.locator('#project-id').fill(PROJECT)
            page.locator('#sessions-list').click()
            page.get_by_text('1 sessions found (first page)').wait_for(timeout=3000)
            assert SESSION in page.locator('#session-list').inner_text()
            assert page.locator('#session-list img').count() == 0
            page.locator('#sessions-watch').click()
            page.get_by_text('Watching project changes').wait_for(timeout=3000)
            assert '#3' in page.locator('#session-changes').inner_text()
            page.locator('#session-list button').click()
            assert page.locator('#workflow').input_value() == SESSION
            page.locator('#sessions-stop').click()
            page.get_by_text('Watch stopped').wait_for(timeout=3000)
            assert not errors,errors
        finally:
            browser.close()
