from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class WeComInbound:
    external_id: str
    sender: str
    content: str


def parse_wecom_message(xml_text: str) -> WeComInbound:
    root = ET.fromstring(xml_text)

    msg_id = _text(root, "MsgId") or f"auto-{int(time.time() * 1000)}"
    sender = _text(root, "FromUserName") or "unknown"
    content = _text(root, "Content") or ""

    return WeComInbound(external_id=msg_id, sender=sender, content=content)


def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    timestamp = str(int(time.time()))
    safe_content = content.replace("<![CDATA[", "").replace("]]>", "")
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{safe_content}]]></Content>"
        "</xml>"
    )


def _text(root: ET.Element, tag: str) -> str | None:
    el = root.find(tag)
    if el is None:
        return None
    if el.text is None:
        return None
    return el.text.strip()
