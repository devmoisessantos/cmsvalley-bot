import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
# preencha com o ID do canal onde o painel vai ficar
CANAL_PAINEL_RECRUTAMENTO_ID = 1486369071590281326
LOGO_PATH = "assets/logo.png"

BACKUP_DIR = os.getenv("BACKUP_DIR", "data/backups")
MAX_BACKUPS_PER_GUILD = int(os.getenv("MAX_BACKUPS_PER_GUILD", 10))
AUTO_BACKUP_INTERVAL_HOURS = int(os.getenv("AUTO_BACKUP_INTERVAL_HOURS", 24))
LOG_CHANNEL_NAME = os.getenv("LOG_CHANNEL_NAME", "backup-logs")
ADMIN_ROLE_NAMES = [
    r.strip() for r in os.getenv("ADMIN_ROLE_NAMES", "Admin,Fundador").split(",") if r.strip()
]
CONFIRMATION_TIMEOUT = int(os.getenv("CONFIRMATION_TIMEOUT", 30))

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN não definido. Crie um arquivo .env baseado em .env.example."
    )


# IDs dos cargos
CARGOS = {

    "Visitantes": 1486368796758507590,
    "ESTUDANTE": 1486368795739291690,
    "PROVA": 1486368794795573308,
    "Aprovado": 1522576790445621301,
    "HP S・Valley": 1486368783835725955,
    "🔰・Enfermeiro (a)": 1486368782585954405,
    "🚑・Paramédico": 1522567683269333012,
    "🥼・Doutor": 1486368765330591754,
    "🩺・Psicólogo": 1486368764059451503,
    "✈️・Recrutador": 1486368762369282179,
    "🚑・Instrutor Resgate": 1506834191126630521,
    "🥼・Instrutor": 1486368762872594534,
    "👑・Responsável Doutor・🥼": 1486368760536240340,
    "👑・Responsável Psicólogo・🧠": 1486368759227613235,
    "👑・Responsável Instrutor・🎓": 1486368758363586592,
    "👑・Responsável Recrutamento・🎯": 1486368757440970803,
    "👑・Responsável Destaque・👑": 1496398147642069063,
    "🛡️・【 GATE 】CMS  ·  Valley": 1515898748763508907,
    "⚔️・【 GATE 】GUARDIÃO": 1515670358261497906,
    "⚔️・【 GATE 】OPERADOR": 1515670175125475469,
    "🛡️・【 GATE 】CAPITÃO": 1515670028999983175,
    "🛡️・【 GATE 】COORDENADOR・TÁTICO": 1515669916785836144,
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO": 1515669807293403217,
    "👑・【 GATE 】COMANDANTE・TÁTICO": 1515669491038818415,
    "👑・SUPERVISOR": 1522581678072004649,
    "👑・VICE DIRETOR": 1522581475118289036,
    "👑・DIRETOR": 1486368748502781983,
    "🔍・COORDENADOR": 1486368746678386688,
    "👑 |  VICE DIRETOR GERAL": 1486368745252192256,
    "👑 |  DIRETOR GERAL": 1486368744195227780,
    "👑 | RESPONSÁVEL GERAL": 1425163342611349574,
    "Responsavel HP": 1325206480541978643,
    "Supervisor NW": 1325206480558882829

}

CARGOS_HIERARQUIA = [

    "Responsavel HP",
    "👑 | RESPONSÁVEL GERAL",
    "👑 |  DIRETOR GERAL",
    "👑 |  VICE DIRETOR GERAL",
    "🔍・COORDENADOR",
    "👑・Responsável Instrutor・🎓",
    "👑・Responsável Recrutamento・🎯",
    "👑・Responsável Psicólogo・🧠",
    "👑・Responsável Doutor・🥼",
    "👑・DIRETOR",
    "👑・VICE DIRETOR",
    "👑・SUPERVISOR",
    "🥼・Instrutor",
    "🚑・Instrutor Resgate",
    "✈️・Recrutador",
    "🩺・Psicólogo",
    "🥼・Doutor",
    "🚑・Paramédico",
    "🔰・Enfermeiro (a)",
]

CARGOS_EXCLUIR_HIERARQUIA = [
    "🛡️・【 GATE 】CMS  ·  Valley",
]

HIERARQUIA_GATE = [

    "👑・【 GATE 】COMANDANTE・TÁTICO",
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
    "🛡️・【 GATE 】COORDENADOR・TÁTICO",
    "🛡️・【 GATE 】CAPITÃO",
    "⚔️・【 GATE 】OPERADOR",
    "⚔️・【 GATE 】GUARDIÃO"
]


# Hierarquia: define quem pode conceder cargos a algum usuário.
HIERARQUIA_CONCESSAO = {

    "Visitantes": None,
    "ESTUDANTE": "✈️・Recrutador",
    "PROVA": "✈️・Recrutador",
    "Aprovado": "✈️・Recrutador",
    "HP S・Valley": None,
    "🔰・Enfermeiro (a)": "✈️・Recrutador",
    "🚑・Paramédico": "✈️・Recrutador",
    "🥼・Doutor": "👑・DIRETOR",
    "🩺・Psicólogo": "👑・DIRETOR",
    "✈️・Recrutador": "👑・DIRETOR",
    "🚑・Instrutor Resgate": "👑・DIRETOR",
    "🥼・Instrutor": "👑・DIRETOR",
    "👑・Responsável Doutor・🥼": "👑 |  VICE DIRETOR GERAL",
    "👑・Responsável Psicólogo・🧠": "👑 |  VICE DIRETOR GERAL",
    "👑・Responsável Instrutor・🎓": "👑 |  VICE DIRETOR GERAL",
    "👑・Responsável Recrutamento・🎯": "👑 |  VICE DIRETOR GERAL",
    "👑・Responsável Destaque・👑": "👑 |  DIRETOR GERAL",
    "⚔️・【 GATE 】GUARDIÃO": "🛡️・【 GATE 】COORDENADOR・TÁTICO",
    "⚔️・【 GATE 】OPERADOR": "🛡️・【 GATE 】COORDENADOR・TÁTICO",
    "🛡️・【 GATE 】CAPITÃO": "🛡️・【 GATE 】COORDENADOR・TÁTICO",
    "🛡️・【 GATE 】COORDENADOR・TÁTICO": "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO": "👑・【 GATE 】COMANDANTE・TÁTICO",
    "👑・【 GATE 】COMANDANTE・TÁTICO": "👑 | RESPONSÁVEL GERAL",
    "👑・SUPERVISOR": "👑・DIRETOR",
    "👑・VICE DIRETOR": "👑 |  VICE DIRETOR GERAL",
    "👑・DIRETOR": "👑 |  VICE DIRETOR GERAL",
    "🔍・COORDENADOR": "👑 |  VICE DIRETOR GERAL",
    "👑 |  VICE DIRETOR GERAL": "👑 |  DIRETOR GERAL",
    "👑 |  DIRETOR GERAL": "👑 | RESPONSÁVEL GERAL",
    "👑 | RESPONSÁVEL GERAL": "Responsavel HP",
    "Responsavel HP": "Supervisor NW"

}

TOTAL_PERGUNTAS_PROVA = 11
COOLDOWN_REPROVACAO_HORAS = 24
TEMPO_LIMITE_PROVA_MINUTOS = 60
NOTA_MINIMA_APROVACAO = 70

CANAIS = {
    "CANAL_PAINEL_PLANTAO_ID": 1531543798293856376, 
    "MANAGE_ROLE_CHANNEL_ID": 1529960097130741801,
    "WHITELIST_CANAL_ID": 1528299364970266657,
    "HIERARQUIA_SUL": 1487250788391583745,
    "MATERIAL_ESTUDO": 1486369061507043348,
    "AVALIACAO": 1486369066091282623,
    "APROVAR_REPROVAR": 1526595318974517340,
    "RECRUTAMENTOS": 1486369074341613638,
    "LOG_WHITELIST": 1528352488028246137,
    "LOG_RECRUTAMENTOS": 1486369287139754014,
    "LOG_APROVACOES": 1526596056274567299,
    "LOG_REPROVACOES": 1526596314744492134,
    "LOG_CARGOS": 1526596799509561404,
    "LOG_ERROS": 1526596982066380990
}

PREFIXOS_NICKNAME = {
    "🔰・Enfermeiro (a)": "[ ENF ]",
    "🚑・Paramédico": "[ PAR ]",
    "🩺・Psicólogo": "[ PSI ]",
    "✈️・Recrutador": "[ REC ]",
    "🥼・Instrutor": "[ INS ]",
    "👑・VICE DIRETOR": "[ V・DIR ]",
    "👑・DIRETOR": "[ DIR ]",
    "🔍・COORDENADOR": "[ COR ]",
    "👑 |  VICE DIRETOR GERAL": "[V.DIR.G]",
    "👑 |  DIRETOR GERAL": "⟦DIR · G⟧",
    "👑 | RESPONSÁVEL GERAL": "⟦RESP · G⟧"
}

ESCOPOS_GERENCIAMENTO = {
    "dono": {
        "cargos_autorizados": [
            "Responsavel HP",
        ],
        "cargos_gerenciaveis": [
            "👑 | RESPONSÁVEL GERAL",
            "👑 |  DIRETOR GERAL",
            "👑 |  VICE DIRETOR GERAL",
            "👑・【 GATE 】COMANDANTE・TÁTICO",
            "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
            "🛡️・【 GATE 】COORDENADOR・TÁTICO",
            "🛡️・【 GATE 】CAPITÃO",
            "⚔️・【 GATE 】OPERADOR",
            "⚔️・【 GATE 】GUARDIÃO",
            "🛡️・【 GATE 】CMS  ·  Valley",            
            "🔍・COORDENADOR",
            "👑・Responsável Instrutor・🎓",
            "👑・Responsável Recrutamento・🎯",
            "👑・Responsável Psicólogo・🧠",
            "👑・Responsável Doutor・🥼",
            "👑・DIRETOR",
            "👑・VICE DIRETOR",
            "👑・SUPERVISOR",
            "🥼・Instrutor",
            "🚑・Instrutor Resgate",
            "✈️・Recrutador",
            "🩺・Psicólogo",
            "🥼・Doutor",
            "🚑・Paramédico",
            "🔰・Enfermeiro (a)",
        ],
    },
    "gate": {
        "cargos_autorizados": [
            "👑・【 GATE 】COMANDANTE・TÁTICO",
            "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
        ],
        "cargos_gerenciaveis": [
            "🛡️・【 GATE 】COORDENADOR・TÁTICO",
            "🛡️・【 GATE 】CAPITÃO",
            "⚔️・【 GATE 】OPERADOR",
            "⚔️・【 GATE 】GUARDIÃO",
            "🛡️・【 GATE 】CMS  ·  Valley",
        ],
    },
    "diretoria": {
        "cargos_autorizados": [
            "🔍・COORDENADOR",
            "👑・DIRETOR",
            "👑・VICE DIRETOR",
            "👑・SUPERVISOR"
        ],
        "cargos_gerenciaveis": {
            "🔍・COORDENADOR": [
                "👑・DIRETOR",
                "👑・VICE DIRETOR",
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ],
            "👑・DIRETOR": [
                "👑・VICE DIRETOR",
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ],
            "👑・VICE DIRETOR": [
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ],
            "👑・SUPERVISOR": [
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ]
        },
    },
    "geral": {
        "cargos_autorizados": [
            "👑 | RESPONSÁVEL GERAL",
            "👑 |  DIRETOR GERAL",
            "👑 |  VICE DIRETOR GERAL"
        ],
        "cargos_gerenciaveis": {
            "👑 | RESPONSÁVEL GERAL": [
                "👑 |  DIRETOR GERAL",
                "👑 |  VICE DIRETOR GERAL",
                "👑・【 GATE 】COMANDANTE・TÁTICO",
                "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
                "🛡️・【 GATE 】COORDENADOR・TÁTICO",
                "🛡️・【 GATE 】CAPITÃO",
                "⚔️・【 GATE 】OPERADOR",
                "⚔️・【 GATE 】GUARDIÃO",
                "🛡️・【 GATE 】CMS  ·  Valley",            
                "🔍・COORDENADOR",
                "👑・Responsável Instrutor・🎓",
                "👑・Responsável Recrutamento・🎯",
                "👑・Responsável Psicólogo・🧠",
                "👑・Responsável Doutor・🥼",
                "👑・DIRETOR",
                "👑・VICE DIRETOR",
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"

            ],
            "👑 |  DIRETOR GERAL": [
                "👑 |  VICE DIRETOR GERAL",
                "👑・【 GATE 】COMANDANTE・TÁTICO",
                "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
                "🛡️・【 GATE 】COORDENADOR・TÁTICO",
                "🛡️・【 GATE 】CAPITÃO",
                "⚔️・【 GATE 】OPERADOR",
                "⚔️・【 GATE 】GUARDIÃO",
                "🛡️・【 GATE 】CMS  ·  Valley",            
                "🔍・COORDENADOR",
                "👑・Responsável Instrutor・🎓",
                "👑・Responsável Recrutamento・🎯",
                "👑・Responsável Psicólogo・🧠",
                "👑・Responsável Doutor・🥼",
                "👑・DIRETOR",
                "👑・VICE DIRETOR",
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ],
            "👑 |  VICE DIRETOR GERAL": [
                "👑・【 GATE 】COMANDANTE・TÁTICO",
                "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
                "🛡️・【 GATE 】COORDENADOR・TÁTICO",
                "🛡️・【 GATE 】CAPITÃO",
                "⚔️・【 GATE 】OPERADOR",
                "⚔️・【 GATE 】GUARDIÃO",
                "🛡️・【 GATE 】CMS  ·  Valley",            
                "🔍・COORDENADOR",
                "👑・Responsável Instrutor・🎓",
                "👑・Responsável Recrutamento・🎯",
                "👑・Responsável Psicólogo・🧠",
                "👑・Responsável Doutor・🥼",
                "👑・DIRETOR",
                "👑・VICE DIRETOR",
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)"
            ],
        },
    },
}


LIMITE_REMOCOES_SUSPEITAS = 5
# 1,5 minutos — mude só este número se quiser ajustar no futuro
JANELA_TEMPO_SUSPEITA_SEGUNDOS = 90

CANAIS_PLANTAO = {
    "CALL_INTERNA": 1486369009153740982,       # preencher com os IDs reais
    "CALL_EXTERNA": 1486369013624865048,
    "BATE_PAPO_1": 1486369021250113630,
    "BATE_PAPO_2": 1486369024878055506,
    "BATE_PAPO_3": 1486369028682547230,
    "DIRETORIA": 1486369036488147015,
    "DIRETORIA_GERAL": 1510825817826132178,
    "RECRUTAMENTO": [
        1486368986374606890, 
        1493444738840526960, 
        1486368989142581359, 
        1486368991600705626
    ],
    "CONSULTORIOS": [
        1486369067383259136, 
        1486369070180995132
    ],
    "SALA_CURSOS": [
        1486369084403876061, 
        1486369087645946006, 
        1486369090279837747
    ],
}

SEGUNDOS_PARA_MOEDA = 10  # 10 segundos pra testar depois mudar
VALOR_MOEDA_INGAME = 100_000
LEMBRETE_1_MINUTOS = 1
LEMBRETE_2_MINUTOS = 2
DESLIGAMENTO_AUTOMATICO_MINUTOS = 3
HOUSEKEEPING_LIMITE_HORAS = 12

def obter_todos_ids_canais_plantao() -> set[int]:
    """Achata CANAIS_PLANTAO (que mistura int único e listas) num set de IDs."""
    ids: set[int] = set()
    for valor in CANAIS_PLANTAO.values():
        if isinstance(valor, list):
            ids.update(valor)
        else:
            ids.add(valor)
    return ids


def obter_ids_canais_plantao_em_ordem() -> list[int]:
    """Achata CANAIS_PLANTAO preservando a ordem de inserção do dicionário
    (Call Interna, Externa, Bate-papo 1-3, Consultórios, Sala de Cursos, Diretoria, Recrutamento...)."""
    ids: list[int] = []
    for valor in CANAIS_PLANTAO.values():
        if isinstance(valor, list):
            ids.extend(valor)
        else:
            ids.append(valor)
    return ids


def _gerar_nomes_amigaveis() -> dict[int, str]:
    """Monta um mapa {channel_id: 'Nome bonito'} — inclusive numerando categorias com lista."""
    nomes: dict[int, str] = {}

    rotulos_unicos = {
        "CALL_INTERNA": "Call Interna",
        "CALL_EXTERNA": "Call Externa",
        "BATE_PAPO_1": "Bate-papo 1",
        "BATE_PAPO_2": "Bate-papo 2",
        "BATE_PAPO_3": "Bate-papo 3",
        "DIRETORIA": "Diretoria",
        "DIRETORIA_GERAL": "Diretoria Geral",
    }
    rotulos_lista = {
        "RECRUTAMENTO": "Recrutamento",
        "CONSULTORIOS": "Consultório",
        "SALA_CURSOS": "Sala de Cursos",
    }

    for chave, valor in CANAIS_PLANTAO.items():
        if isinstance(valor, list):
            base = rotulos_lista.get(chave, chave.title())
            for i, canal_id in enumerate(valor, start=1):
                nomes[canal_id] = f"{base} {i}"
        else:
            nomes[valor] = rotulos_unicos.get(chave, chave.title())

    return nomes


NOMES_CANAIS_PLANTAO = _gerar_nomes_amigaveis()