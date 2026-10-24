"""Orchestrator v2: линейный предсказуемый конвейер вместо фазовой машины.

Дефолт (<=500 файлов): classify(3B) -> plan(3B) -> collect(код) -> budget(код)
  -> synthesize(7B, проза) -> render_diagram(код) -> merge -> critique(3B)+verify(код)
  -> [pass] ответ | [fail] 1x regen(7B).
Deep-mode (>500 файлов или явный «глубоко»): итеративный collector с RAG-досбором,
макс 3-5 итераций, нуджи только при coverage<0.8 по обязательным узлам.
Deterministic fallback: если модель не вызвала инструмент в arch-задаче — код сам
собирает project_map + entry + top-модули (не нуджит в бесконечность).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from agents.critic import critique
from agents.diagram import render_architecture
from agents.evidence import EvidenceItem, EvidenceStore
from agents.model_pool import ModelPool, Role
from agents.verifier import verify
from core.budget import ApproxCounter, OpType, plan_budget, trim_to_tokens
from fs.intel import analyze_project

SMALL_PROJECT_FILES = 100
SMALL_PROJECT_BYTES = 300_000
DEEP_THRESHOLD_FILES = 500
COVERAGE_TARGET = 0.8


@dataclass
class RunResult:
    answer: str
    ascii_diagram: str
    mermaid_diagram: str
    evidence_ids: list[str]
    coverage: float
    unsupported_claims: int
    verify_ok: bool
    critique_ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self, pool: ModelPool, *, intel_cache_dir: Path | None = None, vector_store=None, counter=None
    ) -> None:
        self.pool = pool
        self.counter = counter or ApproxCounter()
        self.intel_cache_dir = intel_cache_dir
        self.vector_store = vector_store
        self._intel_memo: dict[str, Any] = {}

    # ---------- публичный вход ----------
    async def run(
        self,
        user_query: str,
        *,
        project_root: Path,
        history: list,
        on_event: Callable[[str, dict], None] | None = None,
        quality_mode: bool = False,
        deep: bool | None = None,
        time_budget_s: float | None = None,
    ) -> RunResult:
        t0 = time.time()
        ev = on_event or (lambda k, d: None)
        ev("classify", {"query": user_query[:120]})

        task, scope = await self._classify(user_query)
        intel = await self._intel(project_root)
        n_files = intel.file_count
        use_deep = deep if deep is not None else (n_files > DEEP_THRESHOLD_FILES or quality_mode)
        light = n_files <= SMALL_PROJECT_FILES and intel.total_bytes <= SMALL_PROJECT_BYTES
        ev("plan", {"task": task, "scope": scope, "deep": use_deep, "light": light, "files": n_files})

        plan_obj = await self._plan(task, scope, intel)
        evidence = EvidenceStore(self.intel_cache_dir / "evidence.sqlite3" if self.intel_cache_dir else None)
        await self._collect(task, scope, intel, plan_obj, evidence, ev, iterative=use_deep, light=light)

        cov = self._coverage(intel, evidence)
        ev("coverage", {"value": cov})

        # Синтез прозы (7B) — схема НЕ рисуется моделью.
        sys_prompt = self._system_for(task, light=light)
        diagram = render_architecture(intel.graph, intel.entry_points or self._top_roots(intel))
        pack = evidence.pack_for_prompt(budget_tokens=self._evidence_budget(), counter=self.counter)
        dyn = self._dynamic_block(project_root)
        synth_prompt = self._synth_prompt(
            sys_prompt, intel.repomap.as_prompt_block(), pack, diagram.ascii, dyn, user_query, task
        )
        analyst = self.pool.client(Role.ANALYST)
        ev("synthesize", {"role": "analyst", "input_chars": len(synth_prompt)})
        t_synth = time.time()
        prose = await self._gen(analyst, Role.ANALYST, OpType.SYNTHESIS, synth_prompt)
        synth_s = time.time() - t_synth

        answer = self._merge(prose, diagram)
        # Критика + верификация.
        crit = await critique(answer, task, self.pool.client(Role.PLANNER))
        ver = verify(
            answer,
            graph=intel.graph,
            evidence=evidence,
            known_paths=self._known_paths(intel),
            known_symbols=self._known_symbols(intel),
            strict=not light,
        )
        if (not ver.ok or not crit.ok) and not light:
            # Регенерация стоит столько же, сколько синтез (~synth_s).
            # Если она не укладывается в бюджет — берём первый ответ как есть.
            elapsed = time.time() - t0
            if time_budget_s is not None and elapsed + synth_s > time_budget_s:
                ev(
                    "regen_skipped",
                    {
                        "reason": "бюджет времени исчерпан",
                        "elapsed_s": round(elapsed, 1),
                        "synth_s": round(synth_s, 1),
                        "verify_ok": ver.ok,
                        "critique_ok": crit.ok,
                    },
                )
            else:
                ev("regen", {"reason": ver.summary() or crit.hint})
                fix = self._regen_hint(ver, crit)
                prose2 = await self._gen(
                    analyst, Role.ANALYST, OpType.REGEN, synth_prompt + "\n\nИСПРАВЬ: " + fix
                )
                answer2 = self._merge(prose2, diagram)
                ver2 = verify(
                    answer2,
                    graph=intel.graph,
                    evidence=evidence,
                    known_paths=self._known_paths(intel),
                    known_symbols=self._known_symbols(intel),
                    strict=False,
                )
                if ver2.ok or len(ver2.missing_paths) < len(ver.missing_paths):
                    answer, ver = answer2, ver2

        metrics = {
            "elapsed_s": round(time.time() - t0, 1),
            "task": task,
            "files": n_files,
            "light": light,
            "deep": use_deep,
            "evidence_items": len(evidence._order),
            "coverage": cov,
            "unsupported_claims": len(ver.unsupported_claims),
            "verify_ok": ver.ok,
            "critique_ok": crit.ok,
        }
        ev("done", metrics)
        return RunResult(
            answer=answer,
            ascii_diagram=diagram.ascii,
            mermaid_diagram=diagram.mermaid,
            evidence_ids=list(evidence._order),
            coverage=cov,
            unsupported_claims=len(ver.unsupported_claims),
            verify_ok=ver.ok,
            critique_ok=crit.ok,
            metrics=metrics,
        )

    # ---------- этапы ----------
    async def _classify(self, q: str) -> tuple[str, str]:
        planner = self.pool.client(Role.PLANNER)
        prompt = (
            "Определи тип задачи и область. JSON: {\"task\":..., \"scope\":...}. "
            "task ∈ chat|explain_file|architecture|dependencies|bug_analysis|"
            "refactor_plan|search|write_code|iaac_role|terraform_module|vagrant_box. "
            "scope ∈ whole_project|file|symbol. Вопрос: " + q[:600]
        )
        raw = await self._gen(planner, Role.PLANNER, OpType.CLASSIFY, prompt)
        try:
            obj = json.loads(raw if isinstance(raw, str) else raw.content)
            return str(obj.get("task", "chat")), str(obj.get("scope", "whole_project"))
        except Exception:
            ql = q.lower()
            if any(w in ql for w in ("архитект", "структур", "как устро", "компонент", "схем")):
                return "architecture", "whole_project"
            if any(w in ql for w in ("зависимост", "импорт", "who calls", "кто вызывает")):
                return "dependencies", "whole_project"
            if any(w in ql for w in ("роль", "ansible", "playbook")):
                return "iaac_role", "whole_project"
            if any(w in ql for w in ("terraform", "модуль", "ресурс")):
                return "terraform_module", "whole_project"
            if "vagrant" in ql or "vagrantfile" in ql:
                return "vagrant_box", "whole_project"
            return "chat", "whole_project"

    async def _plan(self, task: str, scope: str, intel) -> dict:
        planner = self.pool.client(Role.PLANNER)
        prompt = (
            f"Составь compact JSON-план для задачи '{task}' scope='{scope}'. "
            f"Полезные узлы (top): "
            f"{json.dumps([n.name for n in intel.graph.top_nodes(['class', 'role', 'module_tf', 'resource', 'box', 'file'], 15)], ensure_ascii=False)}. "
            'Формат: {"focus":[...], "depth":1|2, "must_read":[пути]}. '
            'Не выдумывай пути — только из списка.'
        )
        raw = await self._gen(planner, Role.PLANNER, OpType.PLAN, prompt)
        try:
            return json.loads(raw if isinstance(raw, str) else raw.content)
        except Exception:
            return {"focus": intel.entry_points[:5], "depth": 1, "must_read": []}

    async def _collect(
        self, task, scope, intel, plan_obj, evidence: EvidenceStore, ev, *, iterative: bool, light: bool
    ) -> None:
        """Детерминированный сбор доказательств (код, не LLM)."""
        # 1. project_map + entry + manifests как evidence.
        evidence.put(
            EvidenceItem(id="", kind="project_map", content=intel.repomap.as_prompt_block(), relevance=1.0)
        )
        for ep in intel.entry_points[:3]:
            fr = self._file_record(intel, ep)
            if fr:
                excerpt = self._read_excerpt(Path(intel.root) / ep, lines=(1, 60))
                evidence.put(
                    EvidenceItem(
                        id="",
                        kind="entry_point",
                        source_path=ep,
                        start_line=1,
                        end_line=60,
                        content=excerpt,
                        summary=f"Точка входа {ep}",
                        relevance=1.0,
                    )
                )
        for mf in intel.manifests[:4]:
            excerpt = self._read_excerpt(Path(intel.root) / mf, lines=(1, 80))
            evidence.put(EvidenceItem(id="", kind="manifest", source_path=mf, content=excerpt, relevance=0.9))
        # 2. Топ-модули по scoring -> символы + короткие excerpts.
        top = intel.graph.top_nodes(
            ["class", "role", "module_tf", "resource", "box"], limit=10 if light else 16
        )
        for n in top:
            sig = n.metadata.get("signature") or n.name
            evidence.put(
                EvidenceItem(
                    id="",
                    kind="symbol",
                    source_path=n.path,
                    content=sig,
                    summary=f"{n.kind} {n.name}",
                    relevance=0.8 + n.score / 50,
                )
            )
            if not light and n.path:
                ex = self._read_excerpt(Path(intel.root) / n.path, lines=(1, 40))
                evidence.put(
                    EvidenceItem(
                        id="",
                        kind="file_excerpt",
                        source_path=n.path,
                        start_line=1,
                        end_line=40,
                        content=ex,
                        relevance=0.6,
                    )
                )
        # 2.5. Сигнатуры методов top-классов — для архитектурного синтеза
        # они информативнее «шапки файла»: модель видит, ЧТО класс делает,
        # а не только `using`-и и `namespace`.
        top_class_paths = {
            n.path for n in top
            if n.path and n.kind in {"class", "interface", "module", "role"}
        }
        if top_class_paths:
            methods_added = 0
            for n in intel.graph.nodes.values():
                if n.kind != "method" or not n.path or n.path not in top_class_paths:
                    continue
                if methods_added >= 40:
                    break
                sig = n.metadata.get("signature") or n.name
                owner = n.metadata.get("owner_class") or ""
                label = f"{owner}.{n.name}" if owner else n.name
                evidence.put(
                    EvidenceItem(
                        id="",
                        kind="symbol",
                        source_path=n.path,
                        content=f"{label}{sig}",
                        summary=f"метод {label}",
                        relevance=0.65,
                    )
                )
                methods_added += 1
            if methods_added:
                logger.debug(f"Evidence: добавлено {methods_added} сигнатур методов top-классов")
        # 3. Доменные резюме (Ansible/Terraform/Vagrant) — сжатые, не сырой YAML.
        for dsum in self._domain_summaries(intel):
            evidence.put(EvidenceItem(id="", kind="module_summary", content=dsum, relevance=0.85))
        # 4. Dependency edges подграфа — 80 рёбер хватает на «поток данных»
        # (раньше 40 обрезало цепочки CryptoService → KeyManager и т. п.).
        for e in intel.graph.subgraph(intel.entry_points or [n.id for n in top[:3]], depth=2)[:80]:
            evidence.put(
                EvidenceItem(
                    id="",
                    kind="dependency_edge",
                    content=f"{e.source} -[{e.kind}]-> {e.target} (conf={e.confidence})",
                    relevance=0.7,
                )
            )
        # 5. RAG-досбор по focus-термам плана (векторный + BM25 через vector_store).
        focus_terms = plan_obj.get("focus", []) + plan_obj.get("must_read", [])
        if self.vector_store and focus_terms and not light:
            await self._rag_fill(evidence, focus_terms)
        # 6. Итеративный deep-режим: досбор по дырам coverage, макс 3-5, нуджи только при провале.
        if iterative:
            for it in range(3):
                cov = self._coverage(intel, evidence)
                if cov >= COVERAGE_TARGET:
                    break
                gaps = self._coverage_gaps(intel, evidence)
                ev("nudge", {"iter": it, "gaps": gaps[:5], "coverage": cov})
                await self._fill_gaps(evidence, gaps, intel)
        # 7. Deterministic fallback уже встроен: сбор делает код, модель не нужна для чтения.

    async def _rag_fill(self, evidence: EvidenceStore, terms: list[str]) -> None:
        # [M1] VectorStore.search — async-функция, to_thread с ней даёт
        # coroutine-объект (TypeError → молча проглатывалось). Вызываем await.
        try:
            for t in terms[:6]:
                res = await self.vector_store.search(str(t), 4)
                for r in res or []:
                    md = getattr(r, "metadata", None) or {}
                    evidence.put(
                        EvidenceItem(
                            id="",
                            kind="vector_chunk",
                            source_path=md.get("file_path"),
                            start_line=md.get("start_line"),
                            end_line=md.get("end_line"),
                            content=(getattr(r, "text", "") or "")[:1200],
                            relevance=0.5,
                        )
                    )
        except Exception as e:
            logger.warning(f"RAG-досбор не удался: {e}")

    async def _fill_gaps(self, evidence: EvidenceStore, gaps: list[str], intel) -> None:
        for g in gaps[:5]:
            p = Path(intel.root) / g
            if p.exists():
                ex = self._read_excerpt(p, lines=(1, 80))
                evidence.put(
                    EvidenceItem(id="", kind="file_excerpt", source_path=g, content=ex, relevance=0.6)
                )

    # ---------- генерация с бюджетом ----------
    async def _gen(self, client, role: Role, op: OpType, prompt: str) -> str:
        ctx = self.pool.runtime_ctx(role)
        bp = plan_budget(
            ctx_total=ctx,
            op=op,
            counter=self.counter,
            pieces={"system": prompt, "repo_map": "", "evidence": "", "dynamic": "", "history": ""},
        )
        max_tok = bp.output_reserve
        trimmed = trim_to_tokens(self.counter, prompt, bp.available_input)
        spec = self.pool.spec(role)
        grammar = None
        json_schema = None
        if op in (OpType.CLASSIFY, OpType.PLAN, OpType.CRITIQUE):
            json_schema = {"type": "object"}  # structured output для 3B
        res = await client.generate(
            trimmed,
            max_tokens=max_tok,
            temperature=spec.temperature,
            grammar=grammar,
            json_schema=json_schema,
            stream=False,
        )
        return res if isinstance(res, str) else getattr(res, "content", str(res))

    def _evidence_budget(self) -> int:
        """Бюджет evidence-пакета в токенах.

        Раньше было `* 0.45` = ~3000 токенов. На 7B CPU это давало
        TTFT 77 сек (prompt processing 4700 токенов суммарно). Урезаем
        до `* 0.28` = ~1900 токенов: evidence приоритизируется по
        relevance, поэтому самое важное (entry, manifests, top-классы,
        методы) остаётся, а «шапки файлов» и дубли отсекаются.

        Ручной тест на LocalMessenger: TTFT упал с 77 до ~35 сек
        при той же точности «Компонентов» и «Стека».
        """
        ctx = self.pool.runtime_ctx(Role.ANALYST)
        return max(1200, int((ctx - 1100 - 256) * 0.28))

    # ---------- промпты ----------
    def _system_for(self, task: str, *, light: bool) -> str:
        base = (
            "Ты — архитектор кода. Отвечай на русском, Markdown. "
            "Используй ТОЛЬКО предоставленные доказательства [Ei] и граф. "
            "Если факта нет в доказательствах — напиши «не подтверждено». "
            "Готовая ASCII-схема приложена ниже. Если она НЕ отражает "
            "реальную архитектуру (пропущены протоколы обмена, потоки "
            "данных, ключевые роли) — обязательно допиши:\n"
            "  1) какие протоколы используются (TCP/UDP/HTTP/NamedPipe/…);\n"
            "  2) поток данных: ОБЯЗАТЕЛЬНО начни с конкретного вызова "
            "(например, `MainForm.SendMessage → CryptoService.Encrypt → "
            "TcpConnectionManager.Send`), не пиши общих фраз «Program "
            "обрабатывает». Если связь не видна в доказательствах — "
            "отметь «не подтверждено»;\n"
            "  3) связи, которых нет в ASCII-схеме, с указанием [Ei];\n"
            "  4) уточнённую Mermaid-схему, если ASCII недостаточно.\n"
            "НЕ выдумывай имена файлов/классов — только из доказательств.\n"
        )
        if light:
            base += "Режим: компактный проект — ответь по существу, без воды.\n"
        return base

    def _synth_prompt(self, sys_p, repomap, pack, ascii_d, dyn, query, task) -> str:
        return (
            f"{sys_p}\n\n=== ЗАПРОС ===\n{query}\n\n"
            f"=== REPO-MAP ===\n{repomap}\n\n"
            f"=== ДОКАЗАТЕЛЬСТВА ===\n{pack}\n\n"
            f"=== ГОТОВАЯ ASCII-СХЕМА (вставь в ответ как есть, подпиши прозой) ===\n"
            f"```text\n{ascii_d}\n```\n\n"
            f"=== ДИНАМИЧЕСКИЙ КОНТЕКСТ (git/открытые файлы) ===\n{dyn}\n\n"
            f"Структура ответа: 1) Обзор 2) Стек 3) Компоненты (со ссылками [Ei]) "
            f"4) Схема (готовый блок выше) 5) Поток данных 6) Риски/техдолг "
            f"7) Открытые вопросы."
        )

    def _merge(self, prose: str, diagram) -> str:
        # Гарантируем наличие схемы даже если 7B её «потерял».
        if diagram.ascii and diagram.ascii not in prose:
            prose = prose.rstrip() + "\n\n### Схема компонентов\n```text\n" + diagram.ascii + "\n```\n"
        return prose

    def _regen_hint(self, ver, crit) -> str:
        bits = []
        if ver.missing_paths:
            bits.append(f"убери/исправь пути вне индекса: {ver.missing_paths[:5]}")
        if ver.unknown_symbols:
            bits.append("используй только символы из графа: " + ", ".join(ver.unknown_symbols[:5]))
        if ver.unsupported_claims:
            bits.append("добавь [Ei]-ссылки к утверждениям о связях")
        if crit.missing_sections:
            bits.append("добавь секции: " + ", ".join(crit.missing_sections))
        return "; ".join(bits) or "сократи и опирайся на доказательства"

    # ---------- вспомогательное ----------
    async def _intel(self, root: Path):
        key = str(root)
        if key in self._intel_memo:
            return self._intel_memo[key]
        cache = self.intel_cache_dir / "graph.sqlite3" if self.intel_cache_dir else None
        intel = await asyncio.to_thread(analyze_project, root, cache_dir=self.intel_cache_dir)
        # persist графа для инкрементальности
        # persist графа: публичный метод, без доступа к приватным _db/_init_schema.
        if cache is not None:
            try:
                from fs.intel.graph import DependencyGraph  # noqa: F401

                # Пересоздаём граф с persistent-хранилищем (persist создаёт схему).
                persisted = DependencyGraph(db_path=cache)
                persisted.nodes = intel.graph.nodes
                persisted.edges = intel.graph.edges
                persisted.persist()
            except Exception as e:
                logger.debug(f"Graph persist skipped: {e}")
        self._intel_memo[key] = intel
        return intel

    def _coverage(self, intel, evidence: EvidenceStore) -> float:
        required = set(intel.entry_points) | {
            n.path
            for n in intel.graph.top_nodes(["class", "role", "module_tf", "resource", "box"], 8)
            if n.path
        }
        if not required:
            return 1.0
        covered = evidence.all_paths() & required
        return round(len(covered) / len(required), 3)

    def _coverage_gaps(self, intel, evidence) -> list[str]:
        required = set(intel.entry_points) | {
            n.path
            for n in intel.graph.top_nodes(["class", "role", "module_tf", "resource", "box"], 8)
            if n.path
        }
        return sorted(required - evidence.all_paths())

    def _top_roots(self, intel) -> list[str]:
        return [n.id for n in intel.graph.top_nodes(["file", "class", "role", "box", "module_tf"], 3)]

    def _file_record(self, intel, path):
        """Находит узел-файл в графе по относительному пути."""
        return intel.graph.nodes.get(f"file:{path}")

    def _read_excerpt(self, p: Path, *, lines: tuple[int, int]) -> str:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f"(не читается: {p.name})"
        ls = txt.splitlines()
        a, b = lines
        excerpt = "\n".join(ls[a - 1 : b])
        # Для .cs/.csproj — добавим «оглавление»: все public-классы и их
        # публичные методы. Иначе модель видит только шапку файла.
        if p.suffix.lower() == ".cs":
            import re
            head = [
                m.group(0).strip()[:140]
                for m in re.finditer(
                    r"^\s*(?:(?:public|internal|private|protected|sealed|static|abstract|partial)\s+)+"
                    r"(?:class|interface|enum|struct|record)\s+\w+[^{]*",
                    txt, re.M,
                )
            ][:15]
            methods = [
                m.group(0).strip()[:140]
                for m in re.finditer(
                    r"^\s*(?:(?:public|internal|private|protected|static|async|virtual|override|sealed)\s+)+"
                    r"[\w<>\[\],.\s?]+?\s+\w+\s*\([^)]*\)\s*(?:=>|\{)",
                    txt, re.M,
                )
            ][:25]
            if head or methods:
                excerpt = (
                    "// ── Классы ──\n"
                    + "\n".join(f"//   {h}" for h in head)
                    + "\n\n// ── Методы ──\n"
                    + "\n".join(f"//   {m}" for m in methods)
                    + "\n\n" + excerpt
                )
        return excerpt

    def _known_paths(self, intel) -> set[str]:
        return {n.path for n in intel.graph.nodes.values() if n.kind == "file" and n.path}

    def _known_symbols(self, intel) -> set[str]:
        return {
            n.name
            for n in intel.graph.nodes.values()
            if n.kind
            in {
                "class",
                "interface",
                "role",
                "module_tf",
                "resource",
                "box",
                "module",
                "namespace",
                "method",
                "function",
                "task",
                "provisioner",
            }
        }

    def _domain_summaries(self, intel) -> list[str]:
        out: list[str] = []
        d = intel.domain or {}
        ans = d.get("ansible") or {}
        for r in ans.get("roles", []):
            out.append(
                f"Роль {r['name']}: tasks={len(r.get('tasks', []))}, handlers={len(r.get('handlers', []))}, "
                f"deps={[e['to'] for e in ans.get('role_edges', []) if e['from'] == r['name']]}, "
                f"templates={len(r.get('templates', []))}"
            )
        tf = d.get("terraform") or {}
        if tf.get("modules") or tf.get("resources"):
            out.append(
                f"Terraform root: modules={tf.get('modules')}, resources={len(tf.get('resources', []))}, "
                f"vars={len(tf.get('variables', []))}, outputs={len(tf.get('outputs', []))}"
            )
        for v in d.get("vagrant", []):
            chain = " -> ".join((c.get('target', '?')[:30]) for c in v.get("provision_chain", []))
            out.append(
                f"Vagrant {v['path']}: boxes={v.get('boxes')}, providers={v.get('providers')}, "
                f"env={v.get('env_vars')}, prov_chain=[{chain}], техдолг(закомм.)={v.get('commented_provisions')}"
            )
        # C# / .NET домен: csproj (TFM/OutputType/StartupObject),
        # sln (проекты), entry (has_main/launches/readiness_gates).
        for piece in (intel.domain or {}).get("csharp_partial", []) or []:
            if not isinstance(piece, dict):
                continue
            kind = piece.get("kind")
            if kind == "csproj":
                out.append(
                    f"csproj {piece.get('path')}: "
                    f"TFM={piece.get('target_framework')}, "
                    f"OutputType={piece.get('output_type')}, "
                    f"StartupObject={piece.get('startup_object')}, "
                    f"refs={len(piece.get('references') or [])}"
                )
            elif kind == "sln":
                projects = piece.get("projects") or []
                out.append(f"sln {piece.get('path')}: проектов={len(projects)}")
            elif kind == "csharp_file":
                entry = piece.get("entry")
                if entry:
                    out.append(
                        f"entry {piece.get('path')}: "
                        f"has_main={entry.get('has_main')}, "
                        f"launches={entry.get('launches')}, "
                        f"gates={entry.get('readiness_gates')}"
                    )
        return out

    def _dynamic_block(self, root: Path) -> str:
        """git-ветка + diff-stat + последние коммиты (OpenCode-паритет)."""
        import subprocess

        def _run(*args):
            try:
                return subprocess.run(
                    args,
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                ).stdout.strip()
            except Exception:
                return ""

        branch = _run("git", "rev-parse", "--abbrev-ref", "HEAD")
        diffstat = _run("git", "diff", "--stat") or _run("git", "diff", "--cached", "--stat")
        log = _run("git", "log", "--oneline", "-5")
        parts = []
        if branch:
            parts.append(f"branch: {branch}")
        if diffstat:
            parts.append("diff --stat:\n" + diffstat[:1500])
        if log:
            parts.append("recent commits:\n" + log)
        return "\n".join(parts) or "(нет git-контекста)"
