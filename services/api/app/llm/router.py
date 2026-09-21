import os
from time import perf_counter
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import LLMProfile
from app.llm.budget import BUDGET_EXCEEDED_ERROR, estimate_request_cents, profile_budget_decision
from app.llm.ledger import record_llm_call
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.profiles import default_profile_from_settings, get_enabled_profile, profile_is_configured


UNCONFIGURED_ERROR = "LLM profile is not configured"


class LLMRouter:
    def __init__(self, session: Session, transport: httpx.BaseTransport | None = None) -> None:
        self.session = session
        self.transport = transport

    def profile_for(self, task_type: str) -> LLMProfile | None:
        db_profile = get_enabled_profile(self.session, task_type)
        if db_profile is not None:
            return db_profile
        return default_profile_from_settings(get_settings(), task_type)

    def _profile_for_call(self, task_type: str, profile_id: str | None) -> LLMProfile | None:
        if profile_id is None:
            return self.profile_for(task_type)
        return self.session.get(LLMProfile, profile_id)

    def _profile_and_api_key_for_call(
        self,
        task_type: str,
        profile_id: str | None,
    ) -> tuple[LLMProfile | None, str | None]:
        if profile_id is not None:
            profile = self.session.get(LLMProfile, profile_id)
            return profile, self._api_key_from_env(profile)

        db_profile = get_enabled_profile(self.session, task_type)
        if db_profile is not None:
            return db_profile, self._api_key_from_env(db_profile)

        settings = get_settings()
        profile = default_profile_from_settings(settings, task_type)
        if profile is None:
            return None, None
        return profile, settings.llm_api_key or self._api_key_from_env(profile)

    @staticmethod
    def _api_key_from_env(profile: LLMProfile | None) -> str | None:
        if profile is None or not profile.api_key_env_name:
            return None
        return os.getenv(profile.api_key_env_name)

    def chat(
        self,
        *,
        task_type: str,
        messages: list[dict[str, Any]],
        project_id: str | None = None,
        run_id: str | None = None,
        report_id: str | None = None,
        profile_id: str | None = None,
        input_refs: list[str] | None = None,
        output_ref: str | None = None,
        required: bool = False,
    ) -> dict:
        profile, api_key = self._profile_and_api_key_for_call(task_type, profile_id)
        if profile is None or not profile_is_configured(profile, api_key=api_key):
            call = record_llm_call(
                self.session,
                project_id=project_id,
                run_id=run_id,
                report_id=report_id,
                profile_id=profile.id if profile is not None else None,
                task_type=task_type,
                provider=profile.provider if profile is not None else "",
                model=profile.model if profile is not None else "",
                messages=messages,
                input_refs=input_refs,
                output_ref=output_ref,
                usage={},
                latency_ms=None,
                status="skipped",
                error_message=UNCONFIGURED_ERROR,
            )
            if required:
                # required=True 时先提交账本，避免请求异常导致 session 关闭时回滚审计证据。
                self.session.commit()
                raise ValueError(UNCONFIGURED_ERROR)
            return {
                "status": "skipped",
                "call_id": call.id,
                "text": "",
                "usage": {},
                "error_message": UNCONFIGURED_ERROR,
            }

        requested_cents = estimate_request_cents(messages, profile.max_tokens)
        budget_decision = profile_budget_decision(self.session, profile, requested_cents=requested_cents)
        if not budget_decision.allowed:
            usage = {
                "budget": {
                    "used_cents": budget_decision.used_cents,
                    "requested_cents": budget_decision.requested_cents,
                    "limit_cents": budget_decision.limit_cents,
                }
            }
            call = record_llm_call(
                self.session,
                project_id=project_id,
                run_id=run_id,
                report_id=report_id,
                profile_id=profile.id,
                task_type=task_type,
                provider=profile.provider,
                model=profile.model,
                messages=messages,
                input_refs=input_refs,
                output_ref=output_ref,
                usage=usage,
                latency_ms=None,
                status="budget_exceeded",
                error_message=BUDGET_EXCEEDED_ERROR,
            )
            if required:
                # Commit the audit row before raising so required calls remain traceable.
                self.session.commit()
                raise ValueError(BUDGET_EXCEEDED_ERROR)
            return {
                "status": "budget_exceeded",
                "call_id": call.id,
                "text": "",
                "usage": call.usage_json,
                "error_message": BUDGET_EXCEEDED_ERROR,
            }

        client = OpenAICompatibleClient(
            provider=profile.provider,
            api_key=api_key,
            base_url=profile.base_url,
            model=profile.model,
            api_key_required=bool(profile.api_key_required),
            timeout_seconds=profile.timeout_seconds,
            transport=self.transport,
        )

        started = perf_counter()
        try:
            result = client.chat(
                messages=messages,
                max_tokens=profile.max_tokens,
                temperature=profile.temperature,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            call = record_llm_call(
                self.session,
                project_id=project_id,
                run_id=run_id,
                report_id=report_id,
                profile_id=profile.id,
                task_type=task_type,
                provider=profile.provider,
                model=profile.model,
                messages=messages,
                input_refs=input_refs,
                output_ref=output_ref,
                usage={},
                latency_ms=latency_ms,
                status="failed",
                error_message=str(exc),
            )
            if required:
                # required=True 时先提交账本，避免请求异常导致 session 关闭时回滚审计证据。
                self.session.commit()
                raise
            return {
                "status": "failed",
                "call_id": call.id,
                "text": "",
                "usage": {},
                "error_message": str(exc),
            }

        latency_ms = int((perf_counter() - started) * 1000)
        call = record_llm_call(
            self.session,
            project_id=project_id,
            run_id=run_id,
            report_id=report_id,
            profile_id=profile.id,
            task_type=task_type,
            provider=profile.provider,
            model=profile.model,
            messages=messages,
            input_refs=input_refs,
            output_ref=output_ref,
            usage=result.get("usage", {}),
            latency_ms=latency_ms,
            status="succeeded",
            error_message=None,
        )
        return {
            "status": "succeeded",
            "call_id": call.id,
            "text": result.get("text", ""),
            "usage": result.get("usage", {}),
        }
