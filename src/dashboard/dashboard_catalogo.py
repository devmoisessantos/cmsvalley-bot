"""
Catálogo do dashboard: o que cada métrica significa e de onde vem.

A API (cmsvalley-api) lê as MESMAS tabelas do bot. Este módulo é a fonte
de verdade no lado do bot: nomes estáveis, tabela origem, coluna chave e
descrição em português. Quando a API ou o painel Discord precisam
"entender" um KPI, consultam este catálogo (ou o endpoint que o espelha).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Metas oficiais (chaves iguais às da API: ops.METAS_PADRAO / overview)
# ---------------------------------------------------------------------------

METAS_CATALOGO: dict[str, dict] = {
    "productions_month": {
        "rotulo": "Produções no período",
        "unidade": "unidades",
        "inversa": False,
        "cor": "#2ee6c5",
        "icone": "fa-file-medical",
        "origem": {
            "tabelas": ["laudos", "recrutamentos"],
            "regra": (
                "Conta laudos criados no período + recrutamentos com "
                "status APROVADO no período."
            ),
        },
    },
    "shifts_month": {
        "rotulo": "Horas de plantão",
        "unidade": "horas",
        "inversa": False,
        "cor": "#38bdf8",
        "icone": "fa-clock",
        "origem": {
            "tabelas": ["log_plantao"],
            "regra": (
                "Soma duracao_segundos de plantões fechados no período, "
                "convertida em horas."
            ),
        },
    },
    "recruits_month": {
        "rotulo": "Recrutamentos no período",
        "unidade": "pessoas",
        "inversa": False,
        "cor": "#a78bfa",
        "icone": "fa-user-plus",
        "origem": {
            "tabelas": ["recrutamentos"],
            "regra": "Conta recrutamentos iniciados (data_inicio) no período.",
        },
    },
    "response_minutes": {
        "rotulo": "1ª resposta de ticket",
        "unidade": "minutos",
        "inversa": True,
        "cor": "#fbbf24",
        "icone": "fa-headset",
        "origem": {
            "tabelas": ["tickets"],
            "regra": (
                "Média de minutos até a primeira resposta nos tickets "
                "do período. Menor é melhor."
            ),
        },
    },
}

# ---------------------------------------------------------------------------
# Fontes de dados que a API consulta no banco compartilhado
# ---------------------------------------------------------------------------

FONTES_DE_DADOS: dict[str, dict] = {
    "membros": {
        "tabela": "usuarios",
        "descricao": "Membros do hospital (id Discord, nome, status).",
        "usado_em": ["overview.kpis.members", "members", "rankings"],
    },
    "plantao_historico": {
        "tabela": "log_plantao",
        "descricao": "Plantões fechados com duração em segundos.",
        "usado_em": ["overview.kpis.shifts", "shifts", "rankings"],
    },
    "plantao_ao_vivo": {
        "tabela": "estado_plantao",
        "descricao": "Quem está em plantão agora (aberto, sem fim).",
        "usado_em": ["overview.liveNow", "/api/live"],
    },
    "laudos": {
        "tabela": "laudos",
        "descricao": "Laudos psicológicos registrados.",
        "usado_em": ["overview.kpis.productions", "rankings", "member profile"],
    },
    "recrutamentos": {
        "tabela": "recrutamentos",
        "descricao": "Pipeline de recrutamento (status e datas).",
        "usado_em": ["overview.kpis.recruitment", "recruitment view"],
    },
    "tickets": {
        "tabela": "tickets",
        "descricao": "Tickets de suporte / denúncia abertos e fechados.",
        "usado_em": ["overview.kpis.tickets", "tickets view"],
    },
    "historico_cargos": {
        "tabela": "historico_cargos",
        "descricao": "Mudanças de cargo no tempo (promoção, rebaixamento).",
        "usado_em": ["member profile", "audit"],
    },
}

# ---------------------------------------------------------------------------
# Tipos de plantão conhecidos pelo painel web
# ---------------------------------------------------------------------------

TIPOS_DE_PLANTAO: dict[str, dict] = {
    "diurno": {
        "rotulo": "Em call",
        "cor": "#2ee6c5",
        "descricao": "Plantão registrado em log_plantao (sessão de call).",
    },
    "ao_vivo": {
        "rotulo": "Ao vivo agora",
        "cor": "#a78bfa",
        "descricao": "Linha aberta em estado_plantao (ainda sem fim).",
    },
}

# ---------------------------------------------------------------------------
# Comandos que a API enfileira e o bot executa
# ---------------------------------------------------------------------------

COMANDOS_DA_FILA: dict[str, str] = {
    "send_embed": "Publica card no canal do dashboard a partir do payload.",
    "announce": "Envia texto puro no canal configurado.",
    "sync_now": "Recarrega overview e atualiza o painel de KPIs no Discord.",
    "goals_changed": "Metas alteradas no site — republica o painel de KPIs.",
    "settings_changed": "Configurações do site mudaram (só reconhecimento).",
}


def montar_catalogo_para_api() -> dict:
    """
    Pacote completo que o bot pode enviar à API (documentação viva).

    Útil em diagnóstico e para manter o site alinhado com o banco do bot.
    """
    return {
        "metas": METAS_CATALOGO,
        "fontes": FONTES_DE_DADOS,
        "tipos_plantao": TIPOS_DE_PLANTAO,
        "comandos_fila": COMANDOS_DA_FILA,
        "versao_catalogo": 1,
    }
