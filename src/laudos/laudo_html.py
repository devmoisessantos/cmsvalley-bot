"""
Gera o HTML do laudo para envio na DM do requerente.

O visual fica em template_adocao.html (fácil de editar no VS Code).
Este módulo só carrega o template, preenche os campos e embute a logo.
"""

from __future__ import annotations

import base64
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from src.laudos.perguntas_laudo import (
    TIPO_ADOCAO,
    normalizar_tipo_laudo,
    rotulo_tipo,
)

# ---------------------------------------------------------------------------
# Textos fixos (fácil de editar)
# ---------------------------------------------------------------------------

NOME_HOSPITAL = "CENTRO MÉDICO SUL"
TITULO_PORTE = "LAUDO PSICOLÓGICO"
SUBTITULO_PORTE = "Avaliação para porte de arma de fogo"
TITULO_ADOCAO = "LAUDO TÉCNICO PARA ADOÇÃO"
SUBTITULO_ADOCAO = "Avaliação técnica de prontidão para adoção"
RODAPE_PADRAO = (
    "Documento emitido pelo setor de Psicologia do Centro Médico Sul. "
    "Uso exclusivo no contexto do servidor."
)

# Caminhos relativos a este arquivo
_PASTA_LAUDOS = Path(__file__).resolve().parent
_CAMINHO_TEMPLATE = _PASTA_LAUDOS / "template_adocao.html"
# Logo oficial do servidor (assets/logo_cmsvalley.png)
_CAMINHO_LOGO = (
    _PASTA_LAUDOS.parent.parent / "assets" / "logo_cmsvalley.png"
)

_cache_logo_data_uri: str | None = None
_cache_template: str | None = None


def _fuso() -> ZoneInfo:
    try:
        from src.config import TIMEZONE_LOCAL

        return ZoneInfo(TIMEZONE_LOCAL)
    except Exception:
        return ZoneInfo("America/Sao_Paulo")


def _formatar_data_hora(momento: datetime | None) -> str:
    """Data e hora no fuso local, legível."""
    if momento is None:
        momento = datetime.now(tz=_fuso())
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZoneInfo("UTC"))
    local = momento.astimezone(_fuso())
    return local.strftime("%d/%m/%Y às %H:%M")


def _titulo_e_subtitulo(tipo: str) -> tuple[str, str]:
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return TITULO_ADOCAO, SUBTITULO_ADOCAO
    return TITULO_PORTE, SUBTITULO_PORTE


def _classe_parecer(parecer: str) -> str:
    if (parecer or "").strip().upper() == "APROVADO":
        return "aprovado"
    return "reprovado"


def _carregar_template() -> str:
    """Lê template_adocao.html (com cache simples em memória)."""
    global _cache_template
    if _cache_template is not None:
        return _cache_template
    if not _CAMINHO_TEMPLATE.is_file():
        raise FileNotFoundError(
            f"Template HTML não encontrado: {_CAMINHO_TEMPLATE}"
        )
    _cache_template = _CAMINHO_TEMPLATE.read_text(encoding="utf-8")
    return _cache_template


def _logo_data_uri() -> str:
    """
    Logo embutida em base64 para o HTML funcionar offline na DM.

    Se o arquivo não existir, devolve string vazia (a tag img fica sem src).
    """
    global _cache_logo_data_uri
    if _cache_logo_data_uri is not None:
        return _cache_logo_data_uri
    if not _CAMINHO_LOGO.is_file():
        _cache_logo_data_uri = ""
        return _cache_logo_data_uri
    bruto = _CAMINHO_LOGO.read_bytes()
    codificado = base64.b64encode(bruto).decode("ascii")
    _cache_logo_data_uri = f"data:image/png;base64,{codificado}"
    return _cache_logo_data_uri


def montar_html_laudo(
    *,
    laudo_id: int,
    consulta_id: int,
    tipo_laudo: str,
    parecer: str,
    motivo: str,
    registro_profissional: str,
    id_fivem_paciente: str | None,
    id_fivem_psicologo: str | None,
    discord_id_paciente: int,
    discord_id_psicologo: int,
    nome_paciente: str | None = None,
    nome_psicologo: str | None = None,
    criado_em: datetime | None = None,
) -> str:
    """
    Carrega o template, preenche os placeholders e devolve o HTML final.
    """
    tipo_ok = normalizar_tipo_laudo(tipo_laudo)
    titulo, subtitulo = _titulo_e_subtitulo(tipo_ok)
    classe = _classe_parecer(parecer)
    data_hora = _formatar_data_hora(criado_em)

    paciente_exibicao = nome_paciente or f"ID {discord_id_paciente}"
    psicologo_exibicao = nome_psicologo or f"ID {discord_id_psicologo}"

    # Escape em todos os campos de texto para não quebrar o HTML
    substituicoes = {
        "{{LOGO_SRC}}": _logo_data_uri(),
        "{{NOME_HOSPITAL}}": escape(NOME_HOSPITAL),
        "{{TITULO}}": escape(titulo),
        "{{SUBTITULO}}": escape(subtitulo),
        "{{LAUDO_ID}}": escape(str(laudo_id)),
        "{{CONSULTA_ID}}": escape(str(consulta_id)),
        "{{MODALIDADE}}": escape(rotulo_tipo(tipo_ok)),
        "{{DATA_HORA}}": escape(data_hora),
        "{{PACIENTE_NOME}}": escape(paciente_exibicao),
        "{{PACIENTE_PASSAPORTE}}": escape(id_fivem_paciente or "—"),
        "{{PACIENTE_DISCORD}}": escape(str(discord_id_paciente)),
        "{{PSICOLOGO_NOME}}": escape(psicologo_exibicao),
        "{{PSICOLOGO_PASSAPORTE}}": escape(id_fivem_psicologo or "—"),
        "{{PSICOLOGO_DISCORD}}": escape(str(discord_id_psicologo)),
        "{{REGISTRO_PROFISSIONAL}}": escape(registro_profissional or "—"),
        "{{CLASSE_PARECER}}": escape(classe),
        "{{PARECER}}": escape((parecer or "—").strip().upper()),
        "{{MOTIVO}}": escape((motivo or "—").strip()),
        "{{RODAPE}}": escape(RODAPE_PADRAO),
    }

    html = _carregar_template()
    for chave, valor in substituicoes.items():
        html = html.replace(chave, valor)
    return html


def nome_arquivo_laudo(laudo_id: int, tipo_laudo: str | None = None) -> str:
    """Nome do arquivo anexado na DM."""
    tipo_ok = normalizar_tipo_laudo(tipo_laudo)
    if tipo_ok == TIPO_ADOCAO:
        return f"laudo_adocao_{laudo_id}.html"
    return f"laudo_porte_{laudo_id}.html"
