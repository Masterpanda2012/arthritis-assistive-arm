from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Packet:
    command: str
    fields: tuple[str, ...]


def compute_checksum(payload: str) -> int:
    return sum(payload.encode("ascii")) % 256


def build_packet(command: str, *fields: object) -> str:
    payload_parts = [command, *(str(field) for field in fields)]
    payload = ",".join(payload_parts)
    checksum = compute_checksum(payload)
    return f"<{payload}*{checksum}>"


def parse_packet(packet: str) -> Packet:
    if not packet.startswith("<") or not packet.endswith(">"):
        raise ValueError("packet must start with '<' and end with '>'")

    body = packet[1:-1]
    if "*" not in body:
        raise ValueError("packet checksum separator missing")

    payload, checksum_text = body.rsplit("*", 1)
    try:
        checksum = int(checksum_text)
    except ValueError as exc:
        raise ValueError("packet checksum is not an integer") from exc

    expected = compute_checksum(payload)
    if checksum != expected:
        raise ValueError(f"checksum mismatch: expected {expected}, got {checksum}")

    parts = tuple(part.strip() for part in payload.split(","))
    if not parts or not parts[0]:
        raise ValueError("packet command is empty")

    return Packet(command=parts[0], fields=parts[1:])
