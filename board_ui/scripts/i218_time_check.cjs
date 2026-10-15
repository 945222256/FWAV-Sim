/* I-218：跨时区、未来时间、来源不明和采样有效性的语义回归。 */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const BOARD=path.resolve(__dirname,'..'),ROOT=path.resolve(BOARD,'..');
const ts=require(BOARD+'/node_modules/typescript');
const source=fs.readFileSync(BOARD+'/src/console/time.ts','utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2023}}).outputText;
const exportsObject={};vm.runInNewContext(js,{exports:exportsObject,Date,Number,Math});
const {parseTimestamp,ago,sampleFreshness,advanceServerClock}=exportsObject;
const NOW=Date.parse('2026-10-15T08:00:00Z'),checks=[];
function check(name,work){try{const observed=work();checks.push({name,pass:true,observed});}catch(e){checks.push({name,pass:false,error:String(e)});}}
for(const value of ['2026-10-15T08:00:00Z','2026-10-15T16:00:00+08:00','2026-10-15T01:00:00-07:00']){
 check('same_instant_'+value,()=>{assert.equal(parseTimestamp(value).ms,NOW);return NOW;});
}
check('cross_day_age',()=>{const out=ago('2026-10-15T00:58:00-07:00',NOW);assert.equal(out,'2 分钟前');return out;});
check('dst_repeated_hour_is_two_instants',()=>{const a=parseTimestamp('2026-11-01T01:30:00-07:00').ms,b=parseTimestamp('2026-11-01T01:30:00-08:00').ms;assert.equal(b-a,3600000);return b-a;});
for(const value of ['2026-10-15T16:00:00','2026-10-15 16:00:00']){
 check('unqualified_'+value,()=>{assert.equal(parseTimestamp(value).ms,null);const out=ago(value,NOW);assert.match(out,/缺少时区/);assert.equal(sampleFreshness(value,NOW).status,'unknown');return out;});
}
for(const value of ['not-date','2026-02-30T08:00:00Z','2026-10-15T24:00:00Z','2026-10-15T08:00:00+25:00']){
 check('invalid_'+value,()=>{assert.equal(parseTimestamp(value).ms,null);const out=ago(value,NOW);assert.match(out,/无效/);assert.doesNotMatch(out,/NaN/);return out;});
}
check('valid_leap_day',()=>{const value=parseTimestamp('2024-02-29T08:00:00Z').ms;assert.equal(value,Date.parse('2024-02-29T08:00:00Z'));return value;});
check('non_string_fails_closed',()=>{const value=parseTimestamp({bad:'value'});assert.equal(value.ms,null);return value;});
check('missing_time_is_unknown',()=>{const value=sampleFreshness(null,NOW);assert.equal(value.status,'unknown');assert.match(value.message,/未提供/);return value;});
check('future_subsecond_is_not_zero',()=>{const value=ago('2026-10-15T08:00:00.100Z',NOW);assert.match(value,/晚于服务器/);assert.doesNotMatch(value,/0 秒前/);return value;});
check('future_sample_is_unknown',()=>{const value=sampleFreshness('2026-10-15T08:05:00Z',NOW);assert.equal(value.status,'unknown');assert.match(value.message,/晚于服务器/);return value;});
check('twenty_second_boundary',()=>{const value=sampleFreshness('2026-10-15T07:59:40Z',NOW);assert.equal(value.status,'fresh');return value;});
check('past_boundary_is_stale',()=>{const value=sampleFreshness('2026-10-15T07:59:39.999Z',NOW);assert.equal(value.status,'stale');return value;});
check('missing_server_clock',()=>{const value=sampleFreshness('2026-10-15T08:00:00Z',null);assert.equal(value.status,'unknown');assert.match(value.message,/服务器时钟/);return value;});
check('invalid_server_clock',()=>{assert.equal(advanceServerClock(NaN,100,200),null);assert.equal(advanceServerClock(NOW,100,99),null);return null;});
check('monotonic_elapsed_updates_freshness',()=>{const clock=advanceServerClock(NOW,100,25100);assert.equal(clock,NOW+25000);const value=sampleFreshness('2026-10-15T07:59:55Z',clock);assert.equal(value.status,'stale');return value;});
const record={scope:'Pure semantic time cases; fixed instants and a monotonic clock, no production I/O',checks,passed:checks.filter(c=>c.pass).length,total:checks.length};
const out=ROOT+'/logs/ciri_i218_time_fix_20261014';fs.mkdirSync(out,{recursive:true});fs.writeFileSync(out+'/unit_check.json',JSON.stringify(record,null,2));
console.log(JSON.stringify(record,null,2));if(record.passed!==record.total)process.exit(1);
