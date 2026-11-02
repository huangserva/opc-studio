'use strict';
// 固定测试时钟同时传给浏览器夹具、页面日期逻辑和 Python 读取器。
const fixed=process.env.TODAY_E2E_NOW;
if(fixed && !Number.isFinite(Date.parse(fixed)))throw Error('TODAY_E2E_NOW 无效');
const now=()=>fixed?Date.parse(fixed):Date.now();
function pythonClock(){return fixed ? `\nimport datetime as _dt\nclass _Clock(_dt.datetime):\n @classmethod\n def now(cls,tz=None):\n  value=_dt.datetime.fromisoformat(${JSON.stringify(fixed.replace('Z','+00:00'))})\n  return value.astimezone(tz) if tz else value.replace(tzinfo=None)\nimport today_view,interest_channel,video_radar_page_api,wechat_tools_view,official_accounts\nfor _mod in (today_view,interest_channel,video_radar_page_api,wechat_tools_view,official_accounts,grok_bot_radar):_mod.datetime=_Clock\n` : '';}
module.exports={now,pythonClock};
