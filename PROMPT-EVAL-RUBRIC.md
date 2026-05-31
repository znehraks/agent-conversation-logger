# Prompt / Session Qualitative-Evaluation Rubric (v3)

On-demand, point-in-time qualitative analysis of an agent session. Not always-on:
the user requests it at a moment, and the report covers history **up to that point**
(stamp `as-of`). Default scope = **a single session** (one `transcript.md`).

## Output philosophy

**Narrative, not scores.** Each part answers two questions with cited evidence:
**✅ 잘한 점 (what went well)** and **🔧 보완하면 좋을 점 (what to improve, with a concrete how-to)**.
No per-part numeric score and no overall score — a one-line 총평 + a single next action stand in.
Severity is the only quantizer, kept light: 🔴 high · 🟡 medium · 🟢 minor (applied to 🔧 items).

## Rules

- **Evidence required.** Every ✅/🔧 cites the prompt/turn (`P{n}` and/or `ts`).
- **🔧 is actionable.** State the fix, not just the flaw.
- **Field source tags** (for automation): 🧮 = deterministic/heuristic (computed in code) ·
  🤖 = model judgment. Automating the report only sends 🤖 parts to a model; 🧮 parts are computed.
- **Honest about gaps.** Unverified claims, unfinished threads, and dropped coverage are stated, not hidden.

## Report skeleton

```
# 세션 정성분석 — <session_id>
as-of <iso8601> · <agent> · <cwd/role> · prompts N · turns N

## 총평
<one line> ▶ 다음 액션: <single prioritized change>

## A. 입력(프롬프트) 품질      🤖 (+🧮 anti-pattern flags)
- ✅ ...  (P#)
- 🔧 🟡 ...  (P#) → <fix>

## B. 에이전트 응답 품질        🤖
- ✅ ... / 🔧 ...

## C. 마찰·귀책                 🧮 (re-ask/rework/correction counts) + 🤖 (attribution)
- ✅ ... / 🔧 ...

## D. 목표·기록                 🤖
- ✅ 달성/결정로그 ... / 🔧 미완 TODO ...

## E. 토큰·효율                 🧮 (from USAGE sections)
- ✅ ... / 🔧 ...

## 정량 부록                    🧮
- prompts, turns, tools 분포, errors, tokens(in/out/cache, 캐시적중률), 재작업 턴/토큰
```

## Dimensions

| 차원 | 무엇을 보나 | 출처 |
|---|---|---|
| **A 입력 품질** | 요구사항·지시 명확성, 맥락 제공, 범위, 완료조건, 안티패턴(질문 스태킹·모호 대명사·성공기준 부재) | 🤖 + 🧮 |
| **B 응답 품질** | 지시 준수, 과잉/과소 행동, 환각, 검증 습관, 가정의 적절성 | 🤖 |
| **C 마찰·귀책** | 재질문/정정 루프 위치와 귀책(유저 vs 에이전트), 턴 효율, 재작업 | 🧮 + 🤖 |
| **D 목표·기록** | 목표 달성도, 미완 TODO, 결정 로그 | 🤖 |
| **E 토큰·효율** | 총 토큰·캐시 적중률·재작업이 태운 토큰·턴당 평균 (USAGE 섹션 합산) | 🧮 |
| **F 코칭** | 다음 세션 단일 우선순위 + 재사용 프롬프트 템플릿 | 🤖 |

## Deterministic signals (🧮 — computable without a model)

- 되묻기 유발: 어시스턴트가 직후 clarifying question을 던진 유저 프롬프트 수
- 정정 마커: 직후 유저 턴의 `아니/다시/그게 아니라/말고`
- 재작업: 프롬프트 직후 툴 에러(exit≠0/is_error) 또는 같은 작업 반복
- 모호 대명사: `그거/저거/아까/이거 말고` 빈도
- 토큰: USAGE 섹션 합산(in/out/cache_read/cache_write/reasoning/total), 캐시적중률 = cache_read/(in+cache_read)

These flag *candidates*; the 🤖 pass explains why / assigns blame / writes the fix.

## Output file convention

- **Report filename**: `YYYY-MM-DD-eval-<session_id_short>-<role>.md` (e.g.
  `2026-05-31-eval-7d1b0ef0-researcher.md`). Location: `<vault>/agent-logs/prompt-evals/`.
- This name is intentionally **not** `transcript.md`, so the shared viewer (`viewer.html`)
  opens it in **document mode** (formatted markdown), while live transcripts — always written
  as `transcript.md` by the loggers — open in transcript (chat/insights) mode. Detection is
  filename-first, so keep these names stable.

## History note

v1 = per-dimension scores; v2 = 1–5 + weighted letter grade; **v3 (current) = qualitative
잘한 점/보완점, no scores** (per user direction 2026-05-31). If a soft signal is ever wanted
back, prefer severity tags (🔴🟡🟢) over numeric scores.
