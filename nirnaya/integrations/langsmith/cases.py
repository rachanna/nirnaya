"""One LangSmith trace per case: the case's path through the state machine as a tree.

    case K1 · cycle 1
    ├── INGESTED       -> ASSESSED   (the audit body: what was decided at this step)
    ├── INVESTIGATING  -> DECIDED
    │   └── investigate   (model call, from nirnaya.llm.tracing)
    ├── DRAFTING       -> VERIFYING
    │   └── draft         (model call)
    └── ...            -> AWAITING_OUTCOME   (a resting state ends the trace)

The orchestrator advances a case one step per tick, so the root stays open across ticks and
ends when the case reaches a state the machine does not run (REFERRED, AWAITING_OUTCOME,
DEFERRED, CLOSED). A new cycle, or a case resumed after a referral, starts a new root with the
same case metadata. A root still open when the process ends is closed with a note.

It observes only (StepObserver): the machine ignores anything it raises.
"""

import atexit
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from nirnaya.llm.tracing import TracingSettings
from nirnaya.orchestrator.states import RUNNABLE

_RUNNABLE = {s.value for s in RUNNABLE}
Key = tuple[str, str, int]


def _jsonable(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(body, default=str))


class CaseTracer:
    def __init__(
        self,
        settings: TracingSettings,
        *,
        client: Any = None,  # noqa: ANN401 - langsmith.Client
        run_tree: Callable[..., Any] | None = None,
        parent_context: Callable[[Any], AbstractContextManager[None]] | None = None,
    ) -> None:
        if client is None:
            from langsmith import Client

            client = Client(api_key=settings.api_key, api_url=settings.endpoint)
        if run_tree is None:
            from langsmith.run_trees import RunTree

            run_tree = RunTree
        if parent_context is None:
            from langsmith.run_helpers import tracing_context

            def parent_context(parent: Any) -> AbstractContextManager[None]:  # noqa: ANN401
                return tracing_context(parent=parent)

        self.client = client
        self.project = settings.project
        self._run_tree = run_tree
        self._parent = parent_context
        self._roots: dict[Key, Any] = {}
        self._open: dict[Key, Any] = {}
        self._state: dict[Key, str] = {}
        self._last = datetime.min.replace(tzinfo=UTC)
        atexit.register(self.close)

    def _start(self) -> datetime:
        """Strictly increasing start times: quick steps can share a clock tick (Windows), and
        LangSmith orders a trace's runs by start time."""
        self._last = max(datetime.now(UTC), self._last + timedelta(microseconds=1))
        return self._last

    @staticmethod
    def _key(case: Any) -> Key:  # noqa: ANN401 - a CaseRow
        return case.merchant_id, case.case_id, case.cycle

    def _root(self, case: Any) -> Any:  # noqa: ANN401
        key = self._key(case)
        if key not in self._roots:
            meta = {"case_id": case.case_id, "merchant_id": case.merchant_id, "cycle": case.cycle}
            root = self._run_tree(
                name=f"case {case.case_id} · cycle {case.cycle}",
                run_type="chain",
                inputs={**meta, "state": str(case.state)},
                session_name=self.project,
                ls_client=self.client,
                tags=["nirnaya", "case"],
                extra={"metadata": meta},
                start_time=self._start(),
            )
            root.post()
            self._roots[key] = root
        return self._roots[key]

    @contextmanager
    def step(self, case: Any) -> Iterator[None]:  # noqa: ANN401
        key, state = self._key(case), str(case.state)
        self._state[key] = state
        child = self._root(case).create_child(
            name=state,
            run_type="chain",
            inputs={"state": state},
            tags=["nirnaya", "step"],
            start_time=self._start(),
        )
        child.post()
        self._open[key] = child
        try:
            with self._parent(child):  # model calls in this step nest under it
                yield
        except BaseException as e:
            if self._open.pop(key, None) is child:
                child.end(error=f"{type(e).__name__}: {e}")
                child.patch()
            raise
        if self._open.pop(key, None) is child:  # no transition: the case is waiting
            child.end(outputs={"waiting": True})
            child.patch()

    def transition(
        self,
        case: Any,  # noqa: ANN401
        frm: str,
        to: str,
        body: dict[str, Any],
        external: bool,
    ) -> None:
        key = self._key(case)
        root = self._root(case)
        child = self._open.pop(key, None)
        if child is None:  # the watchdog or a failure referral, outside any step
            child = root.create_child(
                name=f"{frm} → {to} (external)",
                run_type="chain",
                inputs={"state": frm},
                tags=["nirnaya", "step", "external"],
                start_time=self._start(),
            )
            child.post()
        child.end(outputs={"from": frm, "to": to, "external": external, **_jsonable(body)})
        child.patch()
        self._state[key] = to
        if to not in _RUNNABLE:
            root.end(outputs={"state": to})
            root.patch()
            del self._roots[key]

    def close(self) -> None:
        """End the traces of cases still in progress, then send everything."""
        for key, root in list(self._roots.items()):
            with suppress(Exception):
                root.end(
                    outputs={
                        "state": self._state.get(key),
                        "note": "process ended; the case continues in a later run",
                    }
                )
                root.patch()
        self._roots.clear()
        with suppress(Exception):
            self.client.flush()
