

# C/C++ 코드 흐름도·위키 생성 시스템 — 통합 구현 플랜

## Context

리포지토리에는 두 개의 설계 문서가 있다.

- **기존 플랜** (`makewiki/makewiki-plan.md`): 프로덕션급 **언어 무관 DeepWiki 클론**. tree-sitter → pgvector RAG → 2단계 LLM 생성 + **LLM이 직접 그린 Mermaid**. 스키마·API·보안·평가·테스트·자체호스팅(DGX/Qwen3)까지 매우 깊다. 스스로 인정한 약점: LLM Mermaid는 환각을 일으키고, 결정론적 콜/CFG 그래프는 v2로 미뤄져 있다.
- **새 플랜**: **C/C++ 특화**, 정확성 우선. **정적 분석(clangd/Joern/SVF)이 진실의 원천**이고 Mermaid는 그래프에서 결정론적으로 생성, 모든 노드가 `file:line`으로 역추적되며 RAG는 보조(GraphRAG)로 강등. 프로덕션 인프라 서술은 얇다.

두 플랜은 상호보완적이다. 기존 플랜의 스키마에는 이미 `code_symbols`/`code_edges` 테이블과 `pipeline/graph.py`·`diagrams.py` 스텁이 있다 — 새 플랜의 정적 분석이 꽂히는 바로 그 자리이며, "v2 보류"에서 "v1 코어"로 승격하면 된다.

**사용자 결정 (이 플랜의 방향을 고정):**
1. **범위: C/C++ 우선, 정적 분석 코어.** 새 플랜의 논지에 전면 커밋. 다국어는 비목표. 벡터 RAG는 얇은 보조.
2. **다이어그램: 결정론 우선.** 콜그래프/CFG의 위상(노드·엣지)은 항상 정적 분석기에서 나오며 LLM 없이 Mermaid로 렌더. LLM은 산문 설명과(선택적) 개념/아키텍처 다이어그램만 담당하고 그래프 노드·엣지를 절대 지어내지 않는다.
3. **인프라: 린 파이프라인 먼저.** 결정론적 코어(빌드 → 콜그래프/CFG → Mermaid + file:line 위키)를 CLI/파이프라인으로 먼저 관통. Postgres/pgvector, FastAPI+Next.js, LLM "Ask", 평가, 자체호스팅은 그 위에 얹는다.

목표: C/C++ 코드베이스에서 (1) 정확한 콜 그래프·CFG를 추출하고, (2) 결정론적으로 Mermaid 흐름도를 렌더하며, (3) 각 노드가 실제 소스(`file:line`)로 역추적되는 위키를 만들고, (4) 자연어 질의에 관련 서브그래프+설명을 반환한다. 비목표: 런타임 동적 분석, 바이너리 리버싱, C/C++ 외 언어.

## 핵심 설계 원칙 (반드시 준수)

- **정적 분석기가 진실의 원천이다.** clangd 인덱스 / Joern CPG / (후순위) SVF가 뽑은 그래프가 사실이다. LLM은 그래프를 서술·시각화만 한다. LLM이 코드를 보고 흐름도를 상상하게 하지 않는다.
- **Mermaid 위상은 결정론 경로로만 생성한다.** 노드·엣지는 그래프에서 코드로 변환한다. LLM은 산문과 개념도에만 쓴다. (기존 플랜 §7·§G의 "LLM Mermaid + repair loop"는 여기서 개념/시퀀스 다이어그램에 한해서만 살아남고, 콜/CFG에는 적용하지 않는다.)
- **모든 노드·서술에 `file:line` 메타를 강제한다.** 결정론 노드는 태생적으로 근거가 붙는다. 근거 검증기(기존 §K citation validator)의 역할은 **LLM 산문에만** 좁혀진다.
- **벡터 임베딩·RAG는 "자연어 질의 → 관련 함수 매칭" 보조 검색으로만.** 구조 파악의 중심은 그래프 순회(GraphRAG)다. (기존 플랜이 RAG를 중심에 둔 대규모 구조 파악 실패를 반복하지 않는다.)
- **LLM CPGQL·원시 쿼리 금지.** Joern 접근은 고수준 함수(`get_call_graph(root, depth)`, `get_cfg(function)`)로만 캡슐화한다.

## 아키텍처 (단계적)

```
[입력] 리포 + .makewiki/config.json (진입점/제외경로/빌드 힌트)
   │
[L0. 빌드 DB]  compile_commands.json 확보 (Bear/CMake) — 실패 시 Joern 빌드리스 폴백
   │
[L1. 정적분석 — 진실의 원천]
   clangd 인덱스(심볼/참조) + Joern CPG(콜그래프/CFG/PDG)  [+ 후순위 SVF: 간접호출]
   → 정규화된 내부 그래프 모델 (노드=함수, 엣지=호출, 메타=file:line·시그니처)
   │
[L2. 그래프 저장 + 서브그래프 추출]
   그래프 저장(초기 SQLite/NetworkX → 규모↑ Postgres code_symbols/code_edges)
   extract_subgraph(root, max_depth, edge_types)  BFS
   + 보조 임베딩(FAISS: 주석/docstring/심볼명만)
   │
[L3. 결정론 렌더 — 핵심 산출물]
   그래프 → Mermaid (callgraph→flowchart, 순서→sequenceDiagram, CFG→flowchart)
   노드에 file:line 부착, 임계 초과 시 서브그래프 분할
   │
[L4. 위키 (구조 먼저 → 상세)]  LLM은 서브그래프를 받아 산문만, 근거 링크 강제
   │
[L5. Ask (GraphRAG)]  자연어 → 후보 함수(임베딩) → 서브그래프 확장 → Mermaid+설명
   │
[증분 캐싱]  변경 파일만 재분석 (clangd 증분 + 그래프 노드 갱신 + content-hash)
```

L0–L3 은 **LLM 없이** 동작하는 결정론 파이프라인 = 최소 제품. L4–L5 만 LLM 계층이며 항상 L1 그래프 위에서만 동작한다.

## 해소된 설계 긴장 (두 플랜 병합의 핵심)

1. **빌드 실행 vs. "무실행" 보안 규칙 (가장 중요).** 새 플랜은 `bear -- make`로 빌드를 **실행**해야 `compile_commands.json`을 얻는다. 그런데 기존 플랜 §I는 신뢰불가 리포에 대해 "install/build/hook 절대 실행 금지(RCE 방지)"를 MVP 요구사항으로 못박는다. **해소책:**
   - **신뢰 리포(로컬/사내, 기본 사용처):** 빌드를 **격리 컨테이너/샌드박스**(네트워크 차단, 읽기전용 마운트 외 격리, 리소스 상한)에서만 실행. `config.build.build_command`는 사용자가 명시적으로 승인한 경우에만 실행.
   - **신뢰불가/공개 리포:** 빌드를 실행하지 않는다. **Joern 빌드리스 파싱**(컴파일 없이 C/C++ CPG 생성)으로 최소 기능을 보장하고, clangd 는 사용자가 제공한 `compile_commands.json`/수동 플래그가 있을 때만 사용. → 이 이중 모드로 "정확도(빌드 실행)"와 "안전(무실행)"을 양립시킨다.
2. **결정론 vs. LLM Mermaid.** 콜/CFG 위상 = 결정론(L3). 개념/아키텍처 다이어그램처럼 정적 근거가 없는 그림만 LLM 허용하되 기존 §J의 render→validate→repair 루프를 강제. 두 플랜이 실제로 여기서 수렴한다(기존 §7 "둘 다 쓴다").
3. **스키마 재사용.** 기존 §B의 `code_symbols`/`code_edges`(rel: calls|imports|inherits|references)를 v2 보류에서 **v1 코어 그래프 저장소**로 승격. CFG 노드/엣지 표현을 위해 `code_edges.rel`에 `cfg_next`·`cfg_branch` 추가, `code_symbols`에 `signature`·시그니처/네임스페이스 메타 컬럼 추가.
4. **GraphRAG vs. 벡터 RAG.** 기존 플랜의 벡터 중심 Ask 를 **GraphRAG 우선**으로 재편: 임베딩은 자연어→후보 함수 매칭에만, 이후 그래프 순회로 서브그래프 확장. 소스 본문 전체 청킹은 하지 않는다(주석/심볼명만 인덱싱).
5. **증분 캐싱 통합.** 기존 §8 Merkle/content-hash 캐시 + 새 플랜의 clangd 증분 인덱스 + 그래프 노드 부분 갱신을 하나의 캐시 계층으로.

## 기술 스택

| 관심사 | 선택 | 근거 |
|---|---|---|
| 정적 분석 (1차) | **clangd** (증분 인덱싱, LSP) | 심볼/참조 정확, 증분 |
| 정적 분석 (통합) | **Joern CPG** (콜그래프/CFG/PDG) | 빌드리스 폴백 가능, 통합 그래프 |
| 정적 분석 (정밀, 후순위) | **SVF** (함수포인터·간접호출) | 콜그래프 정확도 보강 (Phase 6) |
| 빌드 DB | **Bear**(Make) / CMake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` | compile_commands.json |
| 그래프 저장 | 초기 **SQLite/NetworkX 직렬화** → 규모↑ **Postgres**(기존 §B code_symbols/code_edges) | 린 시작, 이관 경로 확보 |
| 보조 임베딩 | **FAISS** + 로컬 임베더(bge-m3/nomic-embed-text) | 주석/심볼명만 인덱싱 |
| 렌더링 | **Mermaid** (flowchart / sequenceDiagram) | 대규모는 서브그래프 분할 |
| 오케스트레이션 | **Python** (CLI 우선 → 이후 FastAPI) | 린 파이프라인 먼저 |
| 이후 계층 | FastAPI+Next.js, arq/Redis, LiteLLM→로컬 vLLM(Qwen3/DGX) | 기존 플랜 §A·§F·§J 그대로 승계 |

도구 정확도·성능은 MVP에서 실측 후 교체 가능하도록 어댑터 인터페이스로 추상화한다(`analysis/*` 래퍼).

## `.makewiki/config.json` 힌트 파일 (새 플랜 채택)

리포 루트에 두는 선언적 힌트 파일. 기존 플랜의 `.devin/wiki.json` steering 개념의 C/C++판.

```json
{
  "build": {
    "compile_commands_path": "build/compile_commands.json",
    "build_command": "bear -- make -j8",
    "run_build": false,
    "exclude_dirs": ["third_party/", "vendor/", "test/fixtures/"]
  },
  "repo_notes": [
    { "content": "core/ 가 핵심. net/ 은 I/O. 우선순위 core > net > util.", "author": "Team Lead" }
  ],
  "diagrams": [
    { "title": "Request Handling Flow", "purpose": "요청→응답 호출 흐름",
      "root_function": "handle_request", "diagram_type": "sequence", "max_depth": 5, "parent": null },
    { "title": "Parser Control Flow", "purpose": "파서 내부 분기/루프",
      "root_function": "parse_input", "diagram_type": "cfg", "max_depth": 3, "parent": "Request Handling Flow" }
  ]
}
```

- `build.run_build`: 기본 `false`. `true`이고 사용자 승인 시에만 샌드박스에서 `build_command` 실행(위 보안 §1).
- `diagram_type`: `callgraph` | `sequence` | `cfg`. `max_depth`로 폭발 방지. `parent`로 계층.
- `diagrams` 제공 시 자동 계획을 건너뛰고 지정분만 생성. 없으면 `repo_notes` 우선순위 기반 자동 계획.
- **검증 한계(조정 가능):** 다이어그램 ≤40, 노트 ≤100, 노트당 ≤10,000자, 제목 고유·비어있지 않음.

## 데이터 모델

린 단계에서는 SQLite/직렬화로 시작하되, 스키마는 기존 §B에서 승격한 형태로 설계해 Postgres 이관을 무마찰로 만든다.

- `code_symbols(id, repo_id, file_id, name, kind, signature, start_line, end_line)` — 함수/심볼 노드, **file:line 필수**.
- `code_edges(id, repo_id, src_id, dst_id, rel)` — `rel ∈ {calls, imports, inherits, references, cfg_next, cfg_branch}`.
- `files(id, repo_id, path, language, sha256, loc)` — 인용·증분 해싱.
- 위키/Ask 계층 추가 시: 기존 §B의 `wiki_pages`, `diagrams(source ∈ {ast, llm})`, `qa_sessions/qa_turns`, 보조 `chunks`(주석/심볼명만, VECTOR) 를 그대로 승계.

## 구현 단계 (Phase)

### Phase 1 — 빌드 DB·정적분석 기반 (MVP 코어)
- [ ] `.makewiki/config.json` 파서·검증기. 스키마·한계 체크, 명확한 에러 메시지.
- [ ] 빌드 DB 확보 모듈. 기존 `compile_commands.json` 탐지, 없으면 (승인 시) 샌드박스 빌드, 그래도 없으면 **Joern 빌드리스 폴백**. CMake/Bear 경로 지원.
- [ ] clangd 연동(LSP): "정의 위치", "이 함수를 호출하는 곳 전부" 정확 반환.
- [ ] Joern CPG 생성·질의 래퍼. 고수준 함수(`get_call_graph(root, depth)`, `get_cfg(function)`)로 CPGQL 캡슐화. LLM에 원시 쿼리 노출 금지.
- [ ] 산출물: 함수 노드(file:line) + 호출 엣지 + CFG를 정규화 그래프 모델로.
- **수용 기준:** 소규모 C 프로젝트(함수 20–50개)에서 콜그래프가 clangd find-references 와 일치. 함수포인터 없는 코드 기준 누락·오탐 0.

### Phase 2 — 그래프 저장·서브그래프 추출·증분
- [ ] 그래프 저장 계층(노드/엣지/메타). SQLite/직렬화 → Postgres 이관 가능 스키마.
- [ ] `extract_subgraph(root_function, max_depth, edge_types)` — BFS, 깊이 제한.
- [ ] 보조 임베딩(FAISS): 주석·docstring·심볼명만. 소스 본문 전체 청킹 금지.
- [ ] 증분: 변경 파일만 재분석(clangd 증분 + 그래프 노드/엣지 부분 갱신 + content-hash 캐시). 기존 §8 Merkle 통합.
- **수용 기준:** "함수 X depth 3 서브그래프"가 관련 노드만 정확 반환. 캐시 히트 시 재분석 스킵 확인.

### Phase 3 — 결정론 Mermaid 렌더 (핵심 산출물, LLM 없음)
- [ ] 그래프→Mermaid 결정론 변환기. callgraph→`flowchart`, 실행순서→`sequenceDiagram`, CFG 분기/루프→`flowchart`.
- [ ] 노드에 file:line 메타 부착(클릭 역추적 가능한 링크/주석).
- [ ] 대규모 분할: 노드 수 임계 초과 시 서브그래프로 쪼개 다중 다이어그램.
- **수용 기준:** 렌더 오류 0, 모든 노드가 그래프 실제 함수와 1:1(환각 노드 0). ← **L0–L3 end-to-end 관통 = 최초 마일스톤.**

### Phase 4 — 위키 생성 (구조 먼저 → 상세, LLM은 산문만)
- [ ] 구조 먼저: 콜그래프·모듈 의존성으로 상위 아키텍처 뼈대(챕터). (기존 2단계 패턴 §E)
- [ ] 각 모듈·함수 상세 페이지 + (결정론) 흐름도 삽입.
- [ ] `diagrams` 있으면 그것만, 없으면 `repo_notes` 우선순위 자동 계획.
- [ ] LLM은 서브그래프를 받아 **산문 설명만** 생성. 모든 서술에 근거 링크(file:line) 강제 + **citation validator**(기존 §K)로 LLM 산문의 경로/심볼 환각 검출·차단.
- **수용 기준:** 각 페이지가 근거 링크 포함, 흐름도가 페이지 주제와 일치, citation validator 통과.

### Phase 5 — Ask 인터페이스 (GraphRAG)
- [ ] 자연어 → 후보 함수(보조 임베딩) → 그래프 순회로 서브그래프 확장(GraphRAG).
- [ ] 서브그래프 → LLM → Mermaid(결정론) + 설명 + 근거 링크.
- [ ] (선택) 스트리밍 응답, 대화 히스토리(기존 §C `/ask` SSE, `qa_turns`).
- **수용 기준:** "함수 X는 어디서 호출되나?", "요청 처리 흐름 그려줘" 류가 정확한 서브그래프 기반으로 응답.

### Phase 6 — 정밀도·프로덕션화 (후순위)
- [ ] **SVF 연동**: wllvm 비트코드 → llvm-link → SVF 콜그래프로 함수포인터·간접호출 보강. 문맥 비민감 분석의 switch-case·함수포인터 오탐 유의.
- [ ] 매크로·조건부 컴파일 검증: 빌드 구성별 흐름도 차이 확인.
- [ ] **프로덕션 인프라 승계**(기존 플랜): FastAPI+Next.js 뷰어, arq/Redis 잡, Postgres/pgvector 이관, LiteLLM→로컬 vLLM(Qwen3/DGX §J), 관측(Langfuse).
- [ ] **보안 하드닝**(기존 §I, C/C++판): 샌드박스 빌드 실행(위 §1), SSRF(리포 URL), 프롬프트 인젝션(리포 텍스트=데이터), 토큰/시크릿 위생, 리소스 상한.
- [ ] **평가/테스트 루프**(기존 §K–§M): citation validator·Mermaid 유효성·coverage 게이트, 골든 리포 스냅샷 테스트(mocked LLM), 10–20 (질의→기대 함수) 셋, DeepWiki 비교 베이스라인(선택, ground truth 아님).
- **수용 기준:** 함수포인터 간접호출이 콜그래프에 반영. 인프라/보안/평가 게이트 동작.

## 디렉터리 구조

```
makewiki/
  config/     # .makewiki/config.json 파서·검증
  build/      # compile_commands.json 확보 (Bear/CMake, 샌드박스 빌드)
  analysis/   # clangd, joern, (svf) 래퍼 — 어댑터 인터페이스
  graph/      # 정규화 그래프 모델·저장·extract_subgraph
  embed/      # 보조 임베딩(FAISS: 주석/심볼명)
  render/     # 그래프 → Mermaid 결정론 변환 + file:line 부착 + 분할
  wiki/       # 구조 먼저 → 페이지 생성 (LLM 산문 + citation validator)
  ask/        # GraphRAG 질의 인터페이스
  cache/      # 정적분석·임베딩·그래프 증분 캐시
  evals/      # (Phase 6) citation/coverage 게이트, 골든 리포
  # Phase 6 이후: api/(FastAPI), frontend/(Next.js), worker/(arq) — 기존 §D 승계
```

## 착수 순서

**L0 → L3 결정론 경로를 먼저 end-to-end 관통**한다: "빌드 DB → 콜그래프/CFG → file:line 붙은 Mermaid"의 최소 파이프라인(CLI). LLM 한 줄 없이 동작하는 이 코어가 최초 마일스톤이다. 그 위에 Phase 2 캐싱, Phase 4–5 LLM 계층(항상 그래프 위에서만), Phase 6 정밀도·프로덕션 인프라를 얹는다. LLM 계층은 정적분석 결과 위에서만 동작하도록 인터페이스를 고정한다.

## 리스크와 대응

- **빌드 실패로 compile_commands.json 미생성:** config 수동 플래그·include 경로 폴백 + Joern 빌드리스로 최소 기능 보장.
- **빌드 실행 = RCE 표면:** 신뢰불가 리포는 무실행(Joern 빌드리스), 신뢰 리포만 샌드박스 실행(위 §1). 기본 `run_build:false`.
- **대규모(10만 라인+) 성능:** 전체 분석 대신 진입점 기준 온디맨드 서브그래프 추출 + 증분 캐싱 필수.
- **LLM CPGQL·그래프 환각:** 원시 쿼리 금지, 고수준 툴만. Mermaid 위상은 결정론. citation validator 로 LLM 산문 검증.
- **Mermaid 가독성:** 대규모는 서브그래프 분할. 자동 레이아웃 한계 감수.

## 검증 (Verification)

- **Phase 1–3 (결정론 코어):** 소규모 C 프로젝트에서 CLI 실행 → 생성된 콜그래프를 `clangd` find-references(또는 `joern`의 독립 쿼리)와 대조해 노드·엣지 일치 확인. 렌더된 Mermaid 를 mermaid-cli 로 파싱해 렌더 오류 0 확인. 모든 노드의 file:line 이 실제 소스 라인과 일치하는지 스팟체크(환각 노드 0).
- **Phase 4–5 (LLM 계층):** 골든 리포에 대해 위키 생성 → citation validator 로 모든 근거 링크가 실존 파일·라인인지 자동 검증(환각 경로 0 게이트). Ask 질의 세트("X는 어디서 호출?", "요청 흐름 그려줘")로 서브그래프 정확성 확인.
- **Phase 6:** 함수포인터 예제에서 SVF-보강 콜그래프가 간접호출을 포함하는지 확인. 샌드박스 빌드가 네트워크 차단·리소스 상한 하에서만 동작하는지, SSRF/인젝션 가드가 §I대로 작동하는지 검증. 골든 셋 회귀 게이트(recall, citation 정확도)로 파이프라인 변경마다 메트릭 델타 확인.

## Project-Aware Importance Lens System (CEO review 2026-07-05)

리뷰 결정으로 확정된 신규 기능. 목표: SPDK 어휘가 하드코딩된 고정 위키 구조를 **프로젝트 유형별로 강조점이 달라지는 렌즈 시스템**으로 전환. 전체 결정 기록은 `~/.gstack/projects/makewiki/ceo-plans/2026-07-05-project-lens-system.md`.

파이프라인 (분류기 스테이지 제거됨):

```
analyzer → CodeGraph → [CodeSignals 추출] → [Lens scoring]
                                                  │
              [WikiPlan: 임계값 넘은 렌즈 union, priority] ◀┘
                        │
              [base lens(항상 켜짐) + 렌즈별 렌더러 모듈] → md
```

- 별도 classifier 없음. profile = 상위 렌즈에서 파생한 라벨(제목/순서용).
- page = 임계값 넘은 렌즈들의 합집합 (multi-label; SPDK = storage + library).
- base lens는 항상-켜짐 바닥 (§9의 공통 질문). 빈 위키 방지.
- **Phase 0 선행 (하이브리드)**: 구조 신호(structs/globals/멤버/함수포인터 필드/includes)는 **결정론 — Joern CPG에서** 뽑음. 지금 `external.py`의 `_dump_script`는 `cpg.method`만 덤프하고 나머지를 버리니, `cpg.typeDecl`/`cpg.member`/globals 쿼리를 추가(빌드 비용 작음, Joern이 이미 파싱함). Joern이 유일한 신호 소스 — heuristic 분석기는 제거됨(2026-07-05), fixture는 데모/테스트 전용(함수+calls만). **LLM으로 구조 신호 추출은 금지** — 환각·비결정론·토큰폭탄·file:line 보증 붕괴·대규모 스케일 불가, plan.md 핵심 원칙과 충돌. LLM은 라벨 계층에만: repo당 1회, **신호 카운트만** 먹여 상위 렌즈 선택(소스/위상/인용 안 만짐), `--llm` 게이트.
- `generator.py`(1140줄)를 base-lens + 렌즈별 모듈로 리팩터, SPDK 어휘는 렌즈별 키워드 팩(데이터)으로.
- 착수는 base + 검증가능한 2렌즈(concurrency, api-contract)를 SPDK + non-storage repo 하나로 증명 후 확장.

미해결 설계 항목: (T3) multi-label 공유 페이지 소유/프레이밍 충돌 해소 규칙, (T4) low-confidence→base-only abstain 게이트.

## Implementation Tasks
CEO 리뷰 findings에서 합성. P1은 ship 차단, P2는 같은 브랜치에서 마무리.

- [ ] **T1 (P1, human: ~3d / CC: ~4h)** — graph — Phase 0 (하이브리드): 구조 신호(structs, globals, members, 함수포인터 필드, includes)를 **Joern CPG에서 결정론 추출** — `_dump_script`에 `cpg.typeDecl`/`cpg.member`/globals 쿼리 추가; Joern이 유일 신호 소스(heuristic 제거됨). LLM 구조 추출 금지.
  - Surfaced by: D1 rider + outside voice #6 + 사용자 질문(가성비) — 구조 사실은 결정론이 런타임 비용·정확도·명제적합 모두 우위, Joern이 이미 파싱함
  - Files: `makewiki/analysis/external.py` (dump script), `makewiki/graph/model.py` (node kinds/edge rels)
  - Verify: 새 node kind/edge rel이 SPDK fixture에서 추출되는지 스냅샷; 재실행 시 동일 출력(결정론)
- [ ] **T1b (P2, human: ~3h / CC: ~30min)** — wiki — (선택) repo당 1회 LLM 라벨 호출: 결정론 신호 카운트만 먹여 상위 렌즈 선택, `--llm` 게이트, 소스/위상/인용 미접촉
  - Surfaced by: 사용자 질문(가성비) — LLM은 판단/라벨 계층에서만 값을 더함
  - Files: `makewiki/llm/*`, `makewiki/wiki/generator.py`
- [ ] **T2 (P1, human: ~1w / CC: ~1d)** — render — generator.py를 base-lens + 렌즈별 렌더러 모듈로 리팩터, SPDK 어휘를 키워드 팩으로
  - Surfaced by: Finding 1A
  - Files: `makewiki/wiki/generator.py`
  - Verify: 기존 8-페이지 SPDK 출력이 회귀 없이 재현
- [ ] **T3 (P1, human: ~2d / CC: ~3h)** — wiki — Lens scoring + WikiPlan(임계값 union, priority), classifier 없음
  - Surfaced by: 1B + T1
  - Files: `makewiki/wiki/generator.py`
- [ ] **T4 (P1, human: ~2h / CC: ~20min)** — wiki — base lens 항상-켜짐 바닥(index+reference+modules 항상)
  - Surfaced by: Finding 2A
  - Files: `makewiki/wiki/generator.py`
  - Verify: 신호 0인 tiny repo에서도 비어있지 않은 위키
- [ ] **T5 (P1, human: ~3h / CC: ~30min)** — wiki — Abstain 게이트: 상위 렌즈 confidence 미달 → base-only + low-confidence 표시
  - Surfaced by: T4 (outside voice #8) — 진짜 위험은 오분류
  - Files: `makewiki/wiki/generator.py`
- [ ] **T6 (P1, human: ~1d / CC: ~2h)** — wiki — multi-label content-merge 시맨틱 명세(공유 index.md 소유 + 공유 심볼 페이지 프레이밍 충돌 해소)
  - Surfaced by: T3 (outside voice #4)
  - Files: `makewiki/wiki/generator.py`
- [ ] **T7 (P2, human: ~4h / CC: ~30min)** — config — config.json profile/lenses 오버라이드 탈출구
  - Surfaced by: Finding 4A
  - Files: `makewiki/config/model.py`, `makewiki/config/loader.py`
- [ ] **T8 (P2, human: ~3h / CC: ~30min)** — wiki — 상위 렌즈 점수 + 근거 file:line을 index(또는 wiki-plan 페이지)에 노출
  - Surfaced by: Finding 8A
  - Files: `makewiki/wiki/generator.py`
- [ ] **T9 (P2, human: ~1d / CC: ~2h)** — tests — 골드-repo 회귀: SPDK + non-storage repo 1개에서 렌즈 점수/페이지 선택 단언
  - Surfaced by: Finding 6A (T1로 재정의)
  - Files: `tests/test_wiki.py`, `tests/fixtures`
- [ ] **T10 (P1, human: ~2h / CC: ~20min)** — render — 검증가능한 2렌즈(concurrency, api-contract) 먼저 구현, 나머지는 인터페이스 스텁
  - Surfaced by: T2 staging

## Eng Review 확정 (2026-07-05)

CEO 리뷰 위에 엔지니어링 리뷰가 확정한 구현 결정 + Codex outside voice가 잡은 갭.

**잠긴 결정:**
- **신호 저장 = 별도 CodeFacts** (Finding 1): 구조 신호(structs/globals/members/includes + 함수당 entrypoint·alloc-free 태그)는 CodeGraph가 아니라 별도 CodeFacts에. 이유: `render/mermaid.py:21`·`generator.py:114`가 모든 노드를 무조건 렌더 → 같은 그래프에 넣으면 콜그래프 오염. CodeGraph는 함수+calls+CFG 그대로.
- **분석기 헬퍼 통합** (Finding 2): `_resolve_call`/`_node_id`/`_is_test_path`를 `analysis/base.py`로 (fixture/external 중복 제거).
- **Joern 테스트 seam** (Finding 3): 녹화된 CPG dump 문자열 fixture로 `_facts_from_joern_dump(str)` 파싱을 Joern 바이너리 없이 테스트. base-lens floor 회귀 테스트는 CRITICAL(무질문).
- **content-merge = blocker** (CT1, Codex 수용): 공유 페이지(index/reference)는 base 렌즈 소유, 렌즈는 자기 섹션만 기여, 심볼 페이지 소유는 단일. **2렌즈 착수 전에** 이 규칙 명세.

**Codex outside voice가 잡은 필수 갭 (아래 태스크로 반영):** CodeFacts 파이프라인 소유자 부재, validator가 fact 인용 거부, 불안정 함수 ID(`file_path::name` → C++ 오버로드 충돌), SQLite 스키마 버저닝 없음, 출력 정리가 새 렌즈 디렉터리 미포함, base 렌즈 페이지 경계 모호, `--llm` 스코프 충돌, config 선반영 필요, typeDecl 필터/includes는 컴파일DB.

## Implementation Tasks (Eng review 추가)

- [ ] **T11 (P1, human: ~1d / CC: ~2h)** — analysis — CodeFacts 파이프라인 소유자: Analyzer가 (CodeGraph, CodeFacts) 반환, CLI store save/load, generate_wiki가 facts 수령. 생성·캐시·무효화·전달 명세
  - Surfaced by: Codex — "CodeFacts has no pipeline owner"
  - Files: `makewiki/analysis/base.py`, `makewiki/graph/store.py`, `makewiki/cli.py`, `makewiki/wiki/generator.py`
- [ ] **T12 (P1, human: ~4h / CC: ~40min)** — wiki — citation 모델을 CodeFacts 앵커(file:line)까지 확장; `validator.py`가 struct/global/member 인용을 유효로 인정
  - Surfaced by: Codex — "validator will reject fact-backed citations" (Finding 1=A의 직접 귀결)
  - Files: `makewiki/wiki/validator.py`
- [ ] **T13 (P1, human: ~3h / CC: ~30min)** — graph — 심볼 키를 file+line+signature로 강화(태그 부착용); `file_path::name`는 C++ 오버로드/템플릿 충돌
  - Surfaced by: Codex — "function tagging depends on unstable IDs"
  - Files: `makewiki/analysis/external.py`, `makewiki/analysis/fixture.py`, `makewiki/graph/model.py`
- [ ] **T14 (P1, human: ~3h / CC: ~30min)** — graph — GraphStore 스키마 버저닝 + code_facts 테이블 마이그레이션; 출력 정리(`_clear_markdown_outputs`)가 새 렌즈 디렉터리도 지우게
  - Surfaced by: Codex — schema versioning + stale cleanup
  - Files: `makewiki/graph/store.py`, `makewiki/wiki/generator.py`
- [ ] **T15 (P1, human: ~1d / CC: ~2h)** — wiki — content-merge 규칙: 공유 페이지(index/reference)=base 소유, 렌즈는 섹션만 기여, 심볼 페이지 단일 소유. **2렌즈 전에 명세**
  - Surfaced by: CT1 (Codex — "content merge is a blocker, not open item")
  - Files: `makewiki/wiki/generator.py`
- [ ] **T16 (P1, human: ~3h / CC: ~30min)** — wiki — base 렌즈 경계 명시: 정확한 페이지·섹션 집합. base가 함수마다 심볼 페이지를 다 내면 노이즈 미감소 — 감축 규칙 정의
  - Surfaced by: Codex — "base lens boundary is vague"
  - Files: `makewiki/wiki/generator.py`
- [ ] **T17 (P1, human: ~4h / CC: ~40min)** — analysis — typeDecl/member 필터: 외부/전방선언/익명/시스템헤더 제외, 정의vs선언 dedupe, repo-root 스코프. includes는 컴파일DB 우선(CPG 신뢰 낮음)
  - Surfaced by: Codex — "type/member extraction needs filtering rules"
  - Files: `makewiki/analysis/external.py`, `makewiki/build/compile_commands.py`
- [ ] **T18 (P2, human: ~3h / CC: ~30min)** — config/llm — `--llm` 라벨 스코프를 openrouter 산문 플래그와 분리; config에 lens threshold/override/disabled/fact-mode 선반영
  - Surfaced by: Codex — LLM plumbing conflict + config compatibility
  - Files: `makewiki/config/model.py`, `makewiki/config/loader.py`, `makewiki/cli.py`, `makewiki/llm/*`
- [ ] **T19 (P1, human: ~1d / CC: ~2h)** — tests — CodeFacts save/load 왕복, 기존 store에서 wiki generate, fact 인용 wiki validate, 결정론 순서/threshold tie/중복 청구/low-conf base-only/facts 없음 케이스; **base-lens floor 회귀(CRITICAL)**
  - Surfaced by: Section 3 test diagram + Codex — persistence/CLI/golden gaps
  - Files: `tests/test_wiki.py`, `tests/test_analyze_render.py`, `tests/fixtures`
- [ ] **T20 (P2, human: ~2h / CC: ~20min)** — analysis — 분석기 공통 헬퍼(`_resolve_call`/`_node_id`/`_is_test_path`)를 `base.py`로 통합
  - Surfaced by: Finding 2 (DRY)
  - Files: `makewiki/analysis/base.py`, `makewiki/analysis/fixture.py`, `makewiki/analysis/external.py`

주의(과소평가된 리스크, Codex): generator.py 리팩터는 단일 태스크가 아니라 **staged migration**(import·테스트·public generate_wiki 동작·출력 형태·검증 가정 전부 변경). 콜그래프 호출 해소가 이미 lossy(`_resolve_call` 첫 매치)라 위상 의존 렌즈 점수가 틀린 엣지 상속 — Phase 0 신호 정확도와 함께 리스크로 추적.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open | SELECTIVE EXPANSION; 8 decisions, 3 cherry-picks, 2 cross-model tensions absorbed |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | eng-plan outside voice: ~20 gaps, folded into T11-T20 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 3 findings resolved (CodeFacts, DRY, Joern fixture) + 1 cross-model tension (merge blocker) + 10 tasks |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | n/a | no UI scope (markdown output) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |

- **CODEX:** eng-plan outside voice ran (46.7k tokens). Biggest catches: validator rejects fact citations, no CodeFacts pipeline owner, unstable function IDs, no schema versioning, content-merge is a blocker. All folded into T11-T20; none silently applied.
- **CROSS-MODEL:** 1 tension surfaced and decided — content-merge promoted from deferred design item to P1 blocker (CT1=A, user accepted Codex). All other Codex findings were additive gaps (agreement), folded as tasks.
- **VERDICT:** CEO + ENG CLEARED — architecture locked (signals→CodeFacts→lens→WikiPlan, base-lens floor, staged 2-lens rollout, merge rule as blocker). Ready to implement T1-T20; start with Phase 0 (T1) + CodeFacts pipeline (T11) + merge rule (T15) before any lens.

NO UNRESOLVED DECISIONS
