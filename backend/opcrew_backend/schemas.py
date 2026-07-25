from typing import Optional

from pydantic import BaseModel, Field


class OpenCodeServerPayload(BaseModel):
    base_url: str = Field(min_length=1)
    username: str = ""
    password: str = ""


class WeComConfigPayload(BaseModel):
    corp_id: str = ""
    agent_id: str = ""
    secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    enabled: bool = True


class UrlConfigPayload(BaseModel):
    url: str = Field(min_length=1)


class TunnelStartPayload(BaseModel):
    local_url: str = "http://127.0.0.1:8011"


class NpcSkillPayload(BaseModel):
    content: str = Field(min_length=1)


class NpcRunPayload(BaseModel):
    server_addr: str = ""
    public_base_url: str = ""
    vkey: str = ""
    conn_type: str = "tcp"
    auto_reconnection: bool = True
    max_conn: int = 1000
    flow_limit: int = 1000
    rate_limit: int = 1000
    basic_username: str = ""
    basic_password: str = ""
    crypt: bool = True
    compress: bool = True
    disconnect_timeout: int = 60
    mode: str = "tcp"
    target_addr: str = "127.0.0.1:18080"
    server_port: int = 10000
    multi_account_line: str = "npc=npc.pwd"
    conf_text: str = ""
    multi_account_text: str = ""


class OpenFlowAnalysisInputPayload(BaseModel):
    reference_video_path: str = ""
    industry: str = ""
    persona: str = ""
    product_info: str = ""
    target_audience: str = ""
    constraints: str = ""
    analysis_goal: str = ""
    video_formula: str = ""


class OpenFlowPromptGeneratePayload(OpenFlowAnalysisInputPayload):
    simple_prompt: str = ""
    final_prompt: str = ""
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    version_name: str = ""
    version_notes: str = ""
    session_id: Optional[int] = None


class OpenFlowPromptSavePayload(BaseModel):
    content: str = Field(min_length=1)


class OpenFlowVersionSavePayload(OpenFlowAnalysisInputPayload):
    session_id: int = Field(gt=0)
    simple_prompt: str = ""
    final_prompt: str = ""
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    version_name: str = ""
    version_notes: str = ""


class OpenFlowVersionLoadPayload(BaseModel):
    session_id: int = Field(gt=0)
    version_id: str = Field(min_length=1)


class OpenFlowVersionDeletePayload(BaseModel):
    session_id: int = Field(gt=0)
    version_id: str = Field(min_length=1)


class OpenFlowAnalysisRunPayload(OpenFlowPromptGeneratePayload):
    final_prompt: str = Field(min_length=1)


class OpenFlowSkillBuilderPayload(BaseModel):
    session_id: int = Field(gt=0)
    content: str = ""
    skill_model_provider: str = ""
    skill_model_id: str = ""
    version_name: str = ""
    version_notes: str = ""


class OpenFlowSkillBuilderGeneratePayload(BaseModel):
    session_id: int = Field(gt=0)
    skill_model_provider: str = ""
    skill_model_id: str = ""


class OpenFlowSkillVersionLoadPayload(BaseModel):
    session_id: int = Field(gt=0)
    version_id: str = Field(min_length=1)


class OpenFlowSkillVersionDeletePayload(BaseModel):
    session_id: int = Field(gt=0)
    version_id: str = Field(min_length=1)
