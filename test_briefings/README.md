# Test Briefings — Prompt Evolution History

Historical briefing snapshots organized by prompt version.
Each directory contains cache JSON (charts + LLM output) and HTML briefings.

Use `--from-cache` to re-render any cached briefing with the current prompt:
```bash
python3 flightweather.py --from-cache test_briefings/v2_with_rules/cache_KBJC_KHCR_2026-03-21 KBJC KHCR 2026-03-21 15:30 18000 --tas 170
```

## Versions

### v1_pre_prompt_rules (sys_len=661)
Minimal system prompt — basic CFI persona, no structured rules.
- KEDC→KBJC 2026-03-19 (Austin to Denver)
- KMQY→KEDC 2026-03-16 (Nashville to Austin)
- KSQL→KVGT 2026-03-13 (San Carlos to Vegas)

### v2_with_rules (sys_len=4906)
Full rule set: moisture gating, primary risk, cause→effect, route/time overlap,
altitude sensitivity, absence of evidence, decision clarity, language precision,
decisiveness, terrain vs system, pilot mental model, AFD integration,
arrival wind analysis, abort criteria, confidence calibration, wind alignment.
- KBJC→KHCR 2026-03-21 (Denver to Heber)
- KHCR→KSQL 2026-03-21 (Heber to San Carlos)
- KSQL→KBDN 2026-03-23 (San Carlos to Bend)
- KBDN→KSQL 2026-03-23 (Bend to San Carlos)
- Also: old-prompt runs of the 2026-03-30 Bend flights for direct comparison

### v3_externalized_enhanced (sys_len=8134)
Prompts externalized to prompts/system.txt and prompts/briefing.txt.
New rules: forecast maturity, required data check, decision triggers,
terrain operationalization, altitude strategy, time-of-day, chart traceability,
final consistency check. Tightened exec summary and "What changes this call".
- KSQL→KBDN 2026-03-30 (San Carlos to Bend)
- KBDN→KSQL 2026-03-30 (Bend to San Carlos)
