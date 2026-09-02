"""
Configuracao central do CMS Valley Bot.

Regra do projeto: nenhum ID, token ou numero magico pode aparecer solto na
logica dos dominios. Tudo mora aqui, e o que muda entre ambientes vem do .env.

Os nomes em maiusculas que estao em ingles (DISCORD_TOKEN, BACKUP_DIR, ...)
sao mantidos porque correspondem as chaves do arquivo .env e a imports que
já existem em dezenas de arquivos. Para cada um deles existe um apelido em
portugues no fim desta secao, que deve ser preferido em codigo novo.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _ler_texto_do_ambiente(nome_da_variavel: str, valor_padrao: str = "") -> str:
    """Le uma variavel de ambiente como texto, sem espacos nas pontas."""
    texto_lido = os.getenv(nome_da_variavel, valor_padrao)
    if texto_lido is None:
        return valor_padrao
    return texto_lido.strip()


def _ler_numero_do_ambiente(nome_da_variavel: str, valor_padrao: int) -> int:
    """
    Le uma variavel de ambiente como numero inteiro.

    Se a variavel nao existir ou estiver vazia, devolve o valor padrao.
    Se estiver preenchida com algo que nao e numero, avisa de forma clara
    em vez de estourar um ValueError sem contexto.
    """
    texto_lido = _ler_texto_do_ambiente(nome_da_variavel)

    if not texto_lido:
        return valor_padrao

    if not texto_lido.lstrip("-").isdigit():
        raise RuntimeError(
            f"A variavel {nome_da_variavel} do .env precisa ser um numero "
            f"inteiro, mas veio '{texto_lido}'."
        )

    return int(texto_lido)


def _ler_lista_de_textos_do_ambiente(
    nome_da_variavel: str,
    valor_padrao: str = "",
) -> list[str]:
    """
    Le uma variavel de ambiente separada por virgulas e devolve uma lista limpa.

    Exemplo: "Admin, Fundador" vira ["Admin", "Fundador"].
    """
    texto_lido = _ler_texto_do_ambiente(nome_da_variavel, valor_padrao)

    lista_de_textos_limpos = []
    for pedaco_bruto in texto_lido.split(","):
        pedaco_limpo = pedaco_bruto.strip()
        if pedaco_limpo:
            lista_de_textos_limpos.append(pedaco_limpo)

    return lista_de_textos_limpos


def _ler_lista_de_numeros_do_ambiente(nome_da_variavel: str) -> list[int]:
    """Le uma variavel de ambiente separada por virgulas e devolve so os numeros."""
    lista_de_numeros = []
    for pedaco_limpo in _ler_lista_de_textos_do_ambiente(nome_da_variavel):
        if pedaco_limpo.isdigit():
            lista_de_numeros.append(int(pedaco_limpo))

    return lista_de_numeros


# ---------------------------------------------------------------------------
# Segredos e identificadores que vem do .env
# ---------------------------------------------------------------------------

DISCORD_TOKEN = _ler_texto_do_ambiente("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN nao definido. Crie um arquivo .env baseado em .env.example."
    )

GUILD_ID = _ler_numero_do_ambiente("GUILD_ID", 0)

if not GUILD_ID:
    raise RuntimeError(
        "GUILD_ID nao definido. Crie um arquivo .env baseado em .env.example."
    )

# Discord da cidade (Valley) — tickets de ocorrência grave de baú.
# Se nao vier no .env, cai no servidor principal em vez de num ID fixo
# de producao escrito no codigo.
GUILD_ID_VALLEY = _ler_numero_do_ambiente("GUILD_ID_VALLEY", GUILD_ID)

DATABASE_URL = _ler_texto_do_ambiente("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL nao definida. Crie um arquivo .env baseado em .env.example."
    )

# Canal onde o painel de recrutamento fica fixado.
# Continua com um padrao para nao quebrar o deploy atual, mas agora pode ser
# trocado pelo .env sem mexer no codigo.
CANAL_PAINEL_RECRUTAMENTO_ID = _ler_numero_do_ambiente(
    "CANAL_PAINEL_RECRUTAMENTO_ID",
    1486369071590281326,
)

# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

BACKUP_DIR = _ler_texto_do_ambiente("BACKUP_DIR", "src/data/backups")
MAX_BACKUPS_PER_GUILD = _ler_numero_do_ambiente("MAX_BACKUPS_PER_GUILD", 10)
# Backup estrutural do Discord (cargos/canais) — intervalo em horas
AUTO_BACKUP_INTERVAL_HOURS = _ler_numero_do_ambiente("AUTO_BACKUP_INTERVAL_HOURS", 24)
# Backup do banco (JSON no LOG_BACKUP) — verificação silenciosa em minutos
AUTO_BACKUP_DB_INTERVAL_MINUTES = _ler_numero_do_ambiente(
    "AUTO_BACKUP_DB_INTERVAL_MINUTES",
    1,
)

# ---------------------------------------------------------------------------
# Permissoes e tempos
# ---------------------------------------------------------------------------

ADMIN_ROLE_NAMES = _ler_lista_de_textos_do_ambiente(
    "ADMIN_ROLE_NAMES",
    "Admin,Fundador",
)
CONFIRMATION_TIMEOUT = _ler_numero_do_ambiente("CONFIRMATION_TIMEOUT", 30)

MESES_ABREV = {
    1: "Jan",
    2: "Fev",
    3: "Mar",
    4: "Abr",
    5: "Mai",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Set",
    10: "Out",
    11: "Nov",
    12: "Dez",
}

# IDs dos cargos
CARGOS = {
    "Visitantes": 1486368796758507590,
    "🚫 Ausente": 1486368753053864058,
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
    "⚠️ EQUIPE • TICKET": 1486368753754308689,
    "👑・SUPERVISOR": 1522581678072004649,
    "👑・VICE DIRETOR": 1522581475118289036,
    "👑・DIRETOR": 1486368748502781983,
    "🔍・COORDENADOR": 1486368746678386688,
    "👑 |  VICE DIRETOR GERAL": 1486368745252192256,
    "👑 |  DIRETOR GERAL": 1486368744195227780,
    "👑 | RESPONSÁVEL GERAL": 1425163342611349574,
    "Responsavel HP": 1325206480541978643,
    "Supervisor NW": 1325206480558882829,
}
# src/config.py
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

# Nomes oficiais dos cargos (promoção, avaliação HP, trilhas).
# Definidos cedo para qualquer módulo poder importar sem depender
# do bloco de metas no final do arquivo.
CARGO_ENFERMEIRO = "🔰・Enfermeiro (a)"
CARGO_PARAMEDICO = "🚑・Paramédico"
CARGO_DOUTOR = "🥼・Doutor"
CARGO_PSICOLOGO = "🩺・Psicólogo"
CARGO_RECRUTADOR = "✈️・Recrutador"
CARGO_INSTRUTOR = "🥼・Instrutor"
CARGO_INSTRUTOR_RESGATE = "🚑・Instrutor Resgate"
CARGO_SUPERVISOR = "👑・SUPERVISOR"
CARGO_VICE_DIRETOR = "👑・VICE DIRETOR"
CARGO_DIRETOR = "👑・DIRETOR"
CARGO_RESP_DOUTOR = "👑・Responsável Doutor・🥼"
CARGO_RESP_PSICOLOGO = "👑・Responsável Psicólogo・🧠"
CARGO_RESP_RECRUTAMENTO = "👑・Responsável Recrutamento・🎯"
CARGO_RESP_INSTRUTOR = "👑・Responsável Instrutor・🎓"
CARGO_COORDENADOR = "🔍・COORDENADOR"
CARGO_VICE_DIRETOR_GERAL = "👑 |  VICE DIRETOR GERAL"
CARGO_DIRETOR_GERAL = "👑 |  DIRETOR GERAL"
CARGO_RESPONSAVEL_GERAL = "👑 | RESPONSÁVEL GERAL"

CARGOS_BYPASS_PRESENCA_CHAMADA = [
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
    "👑・【 GATE 】COMANDANTE・TÁTICO",
    "👑・SUPERVISOR",
    "👑・VICE DIRETOR",
    "👑・DIRETOR",
    "🔍・COORDENADOR",
    "👑 |  VICE DIRETOR GERAL",
    "👑 |  DIRETOR GERAL",
    "👑 | RESPONSÁVEL GERAL",
    "Responsavel HP",
    # Responsáveis de área (mesma regra da Diretoria++ na chamada)
    "👑・Responsável Instrutor・🎓",
    "👑・Responsável Recrutamento・🎯",
    "👑・Responsável Psicólogo・🧠",
    "👑・Responsável Doutor・🥼",
]

CARGOS_DOUTOR_OU_ACIMA = CARGOS_HIERARQUIA[: CARGOS_HIERARQUIA.index("🥼・Doutor") + 1]

# Diretoria++ — painel #gerenciar-membros e ações admin de plantão
CARGOS_DIRETORIA = [
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
]

# ---------------------------------------------------------------------------
# Wipe de temporada (/moderacao wipe)
# ---------------------------------------------------------------------------
# Cargos de gestão que NÃO são kickados e têm restauração automática.
# Responsáveis de área ficam de fora de propósito (recomeçam a temporada).
CARGOS_PRESERVADOS_NO_WIPE = [
    "Responsavel HP",
    "👑 | RESPONSÁVEL GERAL",
    "👑 |  DIRETOR GERAL",
    "👑 |  VICE DIRETOR GERAL",
    "🔍・COORDENADOR",
    "👑・DIRETOR",
    "👑・VICE DIRETOR",
    "👑・SUPERVISOR",
]

# Discord IDs extras sempre preservados (além dos cargos acima).
# Separados por vírgula no .env, opcional.
IDS_PRESERVADOS_NO_WIPE = [
    int(valor)
    for valor in _ler_lista_de_textos_do_ambiente("IDS_PRESERVADOS_NO_WIPE", "")
    if valor.isdigit()
]

# Segundos de espera entre kicks / create_role / create_channel (rate limit).
ATRASO_WIPE_SEGUNDOS = 1.2

# Staff que pode assumir e finalizar tickets
CARGOS_TICKET_STAFF = [
    "⚠️ EQUIPE • TICKET",
] + CARGOS_DIRETORIA

HIERARQUIA_GATE = [
    "👑・【 GATE 】COMANDANTE・TÁTICO",
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
    "🛡️・【 GATE 】COORDENADOR・TÁTICO",
    "🛡️・【 GATE 】CAPITÃO",
    "⚔️・【 GATE 】OPERADOR",
    "⚔️・【 GATE 】GUARDIÃO",
    "🛡️・【 GATE 】CMS  ·  Valley",
]

CARGOS_CRIACAO_EVENTO_GATE = [
    "👑・【 GATE 】COMANDANTE・TÁTICO",
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO",
]

CARGOS_EXCLUIR_HIERARQUIA = [
    "🛡️・【 GATE 】CMS  ·  Valley",
]

CARGOS_PUNICOES = {
    "⛔┇ADV VERBAL ": 1486368789917466765,
    "🚫┇Adv 01": 1486368786646044683,
    "🚫┇Adv 02": 1486368785786077236,
    "🚫┇Adv 03": 1486368784804483252,
    "🚫┇Exonerado": 1486368788956971139,
}

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
    "Responsavel HP": "Supervisor NW",
}

TOTAL_PERGUNTAS_PROVA = 11
COOLDOWN_REPROVACAO_HORAS = 24
TEMPO_LIMITE_PROVA_MINUTOS = 60
NOTA_MINIMA_APROVACAO = 70

# Rankings que geram pagamento (mesmo valor unitário)
VALOR_UNITARIO_RANKING = 100_000  # laudo, recrutamento, chamada
VALOR_POR_RECRUTAMENTO = VALOR_UNITARIO_RANKING  # alias legado
RANKING_HORA_POST = 11  # sábado 11h — fecha ciclo + ranking semanal oficial
RANKING_HORA_REINICIO_TEMPO_REAL_MINUTO = 5  # sábado 11h05 — novo card tempo real
RANKING_HORA_INICIO_CICLO = (
    12  # legado (ciclo semanal de dados continua via ranking_service)
)
RANKING_DIA_POST_MENSAL = 1

# Premiação do ranking de HORAS (plantão) — valores em R$ in-game
# Top 1 → 10: 10M, 6M, 5M, 4M, 3M, 2M, 1M, 1M, 1M, 1M  (total 34M)
# Texto de vitrine usa ~33–34 milhões conforme configuração.
PREMIOS_RANKING_HORAS = [
    10_000_000,  # 1º
    6_000_000,  # 2º
    5_000_000,  # 3º
    4_000_000,  # 4º
    3_000_000,  # 5º
    2_000_000,  # 6º
    1_000_000,  # 7º
    1_000_000,  # 8º
    1_000_000,  # 9º
    1_000_000,  # 10º
]
NOME_PAINEL_RANKING_HORAS_TEMPO_REAL = "ranking_horas_tempo_real"

# Quem NÃO entra no ranking de horas (IDs Discord e cargos por nome em CARGOS)
RANKING_HORAS_IDS_EXCLUIDOS: list[int] = [
    859100649366356000,  # exemplos: 123456789012345678,
    1527878483911512064,
    466874911059869698,
]
# Nomes exatamente como em CARGOS (diretoria / staff fora da disputa)
RANKING_HORAS_CARGOS_EXCLUIDOS: list[str] = [
    "🚫┇Adv 01",
    "🚫┇Adv 02",
    "🚫┇Adv 03",
    "🚫┇Exonerado",
]

# DMs de controle financeiro (IDs Discord, separados por vírgula no .env)
DIRETOR_CONTROLE_FINANCEIRO_IDS = _ler_lista_de_numeros_do_ambiente(
    "DIRETOR_CONTROLE_FINANCEIRO_IDS"
)

# Metadados por área para solicitação no canal de finanças
# responsavel_discord_id / responsavel_fid podem ficar None/"—" até cadastrar
AREAS_FINANCEIRAS = {
    "recrutamento": {
        "titulo": "RECRUTADORES",
        "unidade": "recrutamento",
        "unidade_plural": "recrutamentos",
        "cargo_id": CARGOS.get("👑・Responsável Recrutamento・🎯"),
        "responsavel_discord_id": None,
        "responsavel_fid": "—",
    },
    "laudos": {
        "titulo": "LAUDOS",
        "unidade": "laudo",
        "unidade_plural": "laudos",
        "cargo_id": CARGOS.get("👑・Responsável Psicólogo・🧠"),
        "responsavel_discord_id": None,
        "responsavel_fid": "—",
    },
    "chamadas": {
        "titulo": "CHAMADAS",
        "unidade": "chamada",
        "unidade_plural": "chamadas",
        "cargo_id": CARGOS.get("👑・Responsável Doutor・🥼"),
        "responsavel_discord_id": None,
        "responsavel_fid": "—",
    },
    "plantao_moedas": {
        "titulo": "PLANTÃO (TROCA DE MOEDAS)",
        "unidade": "moeda",
        "unidade_plural": "moedas",
        "cargo_id": None,
        "responsavel_discord_id": None,
        "responsavel_fid": "—",
    },
}

CANAIS = {
    "CANAL_MARCAR_PRESENCA_GATE": 1533997231475261571,
    "CANAL_PAINEL_PLANTAO_ID": 1531543798293856376,  # #iniciar-plantao
    "CANAL_FAZER_CHAMADA": 1486369151952879848,  # #fazer-chamada
    # registro público das chamadas realizadas
    "CANAL_CHAMADAS_HP_SUL": 1486369153349582990,
    "CANAL_GERENCIAR_MEMBROS": 1534803293396795522,  # #gerenciar-membros
    "CANAL_ADVERTENCIAS": 1486369099062837341,
    "CANAL_EXONERACOES": 1486369085829808211,
    "CANAL_PAINEL_LAUDOS": 1486369181132656741,  # painel psicólogos
    "CANAL_LAUDOS": 1486369162455683185,  # laudos publicados
    "CANAL_RANKING_LAUDOS": 1486369179044155685,
    "CANAL_ALERTA_BAU": 1486369103777366106,  # ← alertas de excesso / casos
    # ← painel fixo (admin também usa /bau painel)
    "CANAL_PAINEL_BAU": 1536397321074511872,
    # 0 ate o canal ser criado no deploy
    "CANAL_PAINEL_SCREENSHARE": 1543655341110067200,
    "CANAL_TICKETS_BAU": 1486369093769760843,  # ← canal/categoria de tickets (link DM)
    "CANAL_TICKET_VALLEY": 1077995758785138758,
    "CANAL_FINANCAS": 1486369137021161472,
    "CANAL_AVALIAR_ATENDIMENTO": 1486369031614365856,
    # Promoções e cursos
    "CANAL_PROMOVIDOS": 1486369132071878816,
    "CANAL_NAO_PROMOVIDOS": 1486369133594415194,
    "CANAL_APROVAR_RECUSAR_PROMO": 1533098461430550660,
    "CANAL_REBAIXADOS": 1486369097649492079,
    "CANAL_PAINEL_SOLICITAR_PROMOCAO": 1486369195028517026,
    "CANAL_PAINEL_SOLICITAR_CURSOS": 1486369193787134064,
    "CANAL_AGENDAMENTOS_DE_CURSO": 1533098161827217609,
    "CANAL_APROVAR_REPROVAR_CURSO": 1533112969846722560,
    "CANAL_PAINEL_DEMISSAO": 1486369080465293362,  # painel fixo Solicitar Demissão
    "CANAL_APROVAR_DEMISSAO": 1533884920475160777,  # diretoria aprova / recusa
    # Ausência (afastamento)
    "CANAL_REGISTRAR_AUSENCIA": 1486369010865147924,  # painel fixo Solicitar Ausência
    "CANAL_PEDIDOS_AUSENCIA": 1539021819544211566,  # diretoria aprova / recusa
    "LOG_AUSENCIA": 0,  # opcional: log de ausências aprovadas/negadas (0 = desativado)
    "CANAL_APROVADOS_CURSOS": 1486369199503708271,
    "CANAL_REPROVADOS_CURSOS": 1486369201521295370,
    "MANAGE_ROLE_CHANNEL_ID": 1529960097130741801,
    # ← Canal onde o ranking semanal de recrutadores é postado (todo sábado 11h)
    "RANKING_RECRUTADORES": 1486369056574406736,
    "RANKING_CHAMADAS": 1486369149792948356,
    # Finanças — solicitações de pagamento de área / troca de moedas plantão
    "RANKING_HORAS_PLANTAO": 1534862719457427466,
    # Ranking de moedas (tempo real, mesma ideia do ranking de horas)
    # Configure o ID do canal no .env se for outro: sobrescreva via código ou ajuste
    # aqui
    "RANKING_MOEDAS": 1537382621338800168,
    # Pedidos de depósito $ → moedas (padrão: canal de finanças)
    "CANAL_DEPOSITO_MOEDAS": 1537382938176655410,
    "CRIAR_EVENTO_GATE": 1533993716635799643,
    "WHITELIST_CANAL_ID": 1528299364970266657,
    "HIERARQUIA_SUL": 1487250788391583745,
    "MATERIAL_ESTUDO": 1486369061507043348,
    # Guia do Estagiário — painel de boas-vindas (categoria 1)
    "PAINEL_BOAS_VINDAS": 1486369046357082163,  # ← canal onde o painel fica fixo
    "PAINEL_PUNICOES": 1486369095724171394,
    "GUIA_UNIFORME": 1486369237944635484,
    "GUIA_REGRAS_HP": 1486369111872372746,
    "GUIA_REGRAS_BAU": 1486369113243648072,
    "GUIA_PARCEIROS": 1486369005953486939,
    "GUIA_TUTORIAIS": 1535307402092478514,  # mesmo canal do painel (select abre cards)
    "PAINEL_TUTORIAIS": 1535307402092478514,  # painel fixo de tutoriais
    "GUIA_DUVIDAS_TICKET": 1486369093769760843,
    # Canais citados nos tutoriais (links dos cards)
    "CALL_INTERNA_12": 1486369009153740982,
    "CALL_EXTERNA_13": 1486369013624865048,
    "CALL_AGUARDANDO_CURSO": 1486369072680669327,
    "SOLICITAR_CURSO_RESGATE": 1486369193787134064,
    "MATERIAL_CURSO_RESGATE": 1486369209419038891,
    "SOLICITAR_PROMOCAO_PARAMEDICO": 1486369195028517026,
    "AVALIACAO": 1486369066091282623,
    "APROVAR_REPROVAR": 1526595318974517340,
    "RECRUTAMENTOS": 1486369074341613638,
    "LOG_AUDITORIA_ADMIN": 1535481710295384074,
    "LOG_NOTIFICACOES_DM": 1535522623369384027,
    "LOG_DEMISSAO": 1537425598966792202,  # log de demissões aprovadas/recusadas
    "LOG_RECRUTAMENTOS": 1486369287139754014,
    "LOG_APROVACOES": 1526596056274567299,
    "LOG_REPROVACOES": 1526596314744492134,
    "LOG_PROMOVIDOS": 1536641482952278078,  # promovidos e não promovidos
    "LOG_REBAIXAMENTOS": 1536641973736317029,
    "LOG_CHAMADAS": 1532859432344752149,
    "LOG_WHITELIST": 1528352488028246137,
    "LOG_PUNICOES": 1534935830378840145,
    "LOG_PLANTAO": 1532147151176601670,
    "LOG_CARGOS": 1526596799509561404,
    # Auditoria geral do servidor
    "LOG_CANAIS": 1540387277027410020,
    "LOG_MENSAGENS_DELETADAS": 1540387497450803300,
    "LOG_MENSAGENS_EDITADAS": 1540387629747666944,
    "LOG_VOZ": 1540387734877634630,
    "LOG_APELIDOS": 1540387831229448253,
    "LOG_MEMBROS": 1540386710939238490,
    "LOG_AVATARES": 1540387918181572658,
    "LOG_MODERACAO": 1540388020354678834,
    "LOG_HORAS": 1540388094858108938,  # plantão: início/fim/duração/ajustes
    "LOG_LAUDO": 1536242617573183508,
    "LOG_BAU": 1486369263479423047,  # logs do baú (servidor)
    "LOG_ERROS": 1526596982066380990,
    # 0 ate o canal de log ser criado no deploy
    "LOG_SCREENSHARE": 1543653722587136140,
    "LOG_TICKETS": 1486369268411924684,
    "LOG_GATE": 1533997859790127345,
    "LOG_BACKUP": 1523367341096697996,
    # Painel fixo: enviar notificação por DM (Diretoria++)
    "CANAL_ENVIAR_NOTIFICACAO": 1512277526498639902,
    # Tickets — painéis de abertura (fixos)
    "CANAL_ABRIR_SUPORTE_DUVIDAS": 1486369093769760843,
    "CANAL_ABRIR_TICKET_DENUNCIAS": 1537886870183215255,
    # Tickets — categorias Discord (onde os canais são criados)
    "CATEG_SUPORTE_DUVIDAS": 1486368963192688753,
    "CATEG_REVOGAR_ADV": 1486368965725786225,
    "CATEG_REVOGAR_EXO": 1486368967193788580,
    "CATEG_DENUNCIAS_JOGADOR": 1486368965084315679,
    "CATEG_DENUNCIAS_DIRETORIA": 1537884732358922280,
    # Entrada e saída de membros no servidor (cards automáticos)
    "CANAL_BOAS_VINDAS": 1539222373717381150,
    "CANAL_ADEUS_SERVIDOR": 1539222407947100200,
}

# ---------------------------------------------------------------------------
# Tickets — definição das categorias por segmento (painel)
# ---------------------------------------------------------------------------
# Cada item: chave interna, rótulo no select, emoji, categoria Discord, painel
TICKETS_CATEGORIAS = {
    "suporte_duvidas": {
        "rotulo": "Suporte / Dúvidas",
        "emoji": "🙋",
        "categoria_config": "CATEG_SUPORTE_DUVIDAS",
        "segmento": "suporte",
        "prefixo_canal": "🙋・suporte",
        # Sem lista = qualquer membro pode abrir
        "cargos_obrigatorios": [],
    },
    "revogar_adv": {
        "rotulo": "Revogar Advertência",
        "emoji": "📝",
        "categoria_config": "CATEG_REVOGAR_ADV",
        "segmento": "suporte",
        "prefixo_canal": "📝・revogar-adv",
        # Precisa ter AO MENOS um destes cargos (CARGOS_PUNICOES)
        "cargos_obrigatorios": [
            "⛔┇ADV VERBAL ",
            "🚫┇Adv 01",
            "🚫┇Adv 02",
        ],
    },
    "revogar_exo": {
        "rotulo": "Revogar Exoneração",
        "emoji": "🔓",
        "categoria_config": "CATEG_REVOGAR_EXO",
        "segmento": "suporte",
        "prefixo_canal": "🔓・revogar-exo",
        "cargos_obrigatorios": [
            "🚫┇Adv 03",
            "🚫┇Exonerado",
        ],
    },
    "denuncias_jogador": {
        "rotulo": "Denúncias Jogador",
        "emoji": "⛔",
        "categoria_config": "CATEG_DENUNCIAS_JOGADOR",
        "segmento": "denuncias",
        "prefixo_canal": "⛔・denuncia-jogador",
        "cargos_obrigatorios": [],
    },
    "denuncias_diretoria": {
        "rotulo": "Denúncias Diretoria",
        "emoji": "🛡️",
        "categoria_config": "CATEG_DENUNCIAS_DIRETORIA",
        "segmento": "denuncias",
        "prefixo_canal": "🛡️・denuncia-diretoria",
        "cargos_obrigatorios": [],
    },
}

# URLs de imagens da MediaGallery do painel de boas-vindas (até 10).
# Deixe vazio [] se ainda não tiver as imagens; o painel funciona sem galeria.
GUIA_BOAS_VINDAS_GALLERY: list[str] = [
    "https://cdn.discordapp.com/attachments/1443642763470962759/1534719249845715196/CENTRO_MEDICO_SUL.gif?ex=6a75262e&is=6a73d4ae&hm=eaf00b8359cf6cbb18569926dde0a7a85f0d253ffee9701ec080206d4ec96f26&",
]
GUIA_DE_TUTORIAIS: list[str] = [
    "https://cdn.discordapp.com/attachments/1535352666375462932/1535353764343447632/GUIA_DE_TUTORIAIS.gif?ex=6a77751e&is=6a76239e&hm=685fb1733a1818eb8db3d6e0ad4446f41f35da9055e0949493567b228fce4b9a&"
]

PREFIXOS_NICKNAME = {
    "🔰・Enfermeiro (a)": "[ ENF ]",
    "🚑・Paramédico": "[ PAR ]",
    "🥼・Doutor": "[ DR ]",
    "🩺・Psicólogo": "[ PSI ]",
    "✈️・Recrutador": "[ REC ]",
    "🥼・Instrutor": "[ INS ]",
    "🚑・Instrutor Resgate": "[ INS · R ]",
    "👑・VICE DIRETOR": "[ V・DIR ]",
    "👑・DIRETOR": "[ DIR ]",
    "🔍・COORDENADOR": "[ COR ]",
    "👑・【 GATE 】COMANDANTE・TÁTICO": "【CMD · GATE】",
    "👑・【 GATE 】SUBCOMANDANTE・TÁTICO": "【SUB · GATE】",
    "🛡️・【 GATE 】COORDENADOR・TÁTICO": "【COR · GATE】",
    "🛡️・【 GATE 】CAPITÃO": "【CAP · GATE】",
    "⚔️・【 GATE 】OPERADOR": "【OP · GATE】",
    "⚔️・【 GATE 】GUARDIÃO": "【G · GATE】",
    "👑 |  VICE DIRETOR GERAL": "[V.DIR・G]",
    "👑 |  DIRETOR GERAL": "⟦DIR · G⟧",
    "👑 | RESPONSÁVEL GERAL": "⟦RESP · G⟧",
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
            "👑・SUPERVISOR",
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
                "🔰・Enfermeiro (a)",
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
                "🔰・Enfermeiro (a)",
            ],
            "👑・VICE DIRETOR": [
                "👑・SUPERVISOR",
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)",
            ],
            "👑・SUPERVISOR": [
                "🥼・Instrutor",
                "🚑・Instrutor Resgate",
                "✈️・Recrutador",
                "🩺・Psicólogo",
                "🥼・Doutor",
                "🚑・Paramédico",
                "🔰・Enfermeiro (a)",
            ],
        },
    },
    "geral": {
        "cargos_autorizados": [
            "👑 | RESPONSÁVEL GERAL",
            "👑 |  DIRETOR GERAL",
            "👑 |  VICE DIRETOR GERAL",
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
                "🔰・Enfermeiro (a)",
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
                "🔰・Enfermeiro (a)",
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
                "🔰・Enfermeiro (a)",
            ],
        },
    },
}


LIMITE_REMOCOES_SUSPEITAS = 5
# 1,5 minutos — mude só este número se quiser ajustar no futuro
JANELA_TEMPO_SUSPEITA_SEGUNDOS = 90

CANAIS_PLANTAO = {
    "CALL_INTERNA": 1486369009153740982,  # preencher com os IDs reais
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
        1486368991600705626,
    ],
    "CONSULTORIOS": [1486369067383259136, 1486369070180995132],
    "SALA_CURSOS": [1486369084403876061, 1486369087645946006, 1486369090279837747],
}

# Plantão — tempo em call para creditar 1 moeda (30 minutos)
SEGUNDOS_PARA_MOEDA = 1800
VALOR_MOEDA_INGAME = 100_000

# Plantão — ociosidade (fora de call com toggle ligado)
# O loop verifica a cada 1 minuto; avisos em 10 / 15 / 25; desliga em 30.
LEMBRETE_1_MINUTOS = 10
LEMBRETE_2_MINUTOS = 15
LEMBRETE_3_MINUTOS = 25
DESLIGAMENTO_AUTOMATICO_MINUTOS = 30
HOUSEKEEPING_LIMITE_HORAS = 6

# Plantão — AFK (mudo + surdo no mesmo canal)
AFK_AVISO_MINUTOS = 170  # avisa 10 min antes do corte (170 = 2h50)
AFK_LIMITE_MINUTOS = 180  # 3 horas — desconecta e penaliza
PENALIDADE_AFK_MOEDAS = 3


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
    """
    Monta um mapa {channel_id: 'Nome bonito'} — inclusive numerando categorias com
    lista.
    """
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
            for indice, canal_id in enumerate(valor, start=1):
                nomes[canal_id] = f"{base} {indice}"
        else:
            nomes[valor] = rotulos_unicos.get(chave, chave.title())

    return nomes


NOMES_CANAIS_PLANTAO = _gerar_nomes_amigaveis()


TIMEZONE_LOCAL = "America/Sao_Paulo"  # ajuste se o fuso do servidor/cidade for outro
RR_HORARIOS = ["11:00", "17:00"]  # horários diários de RR (restart) da cidade
INTERVALO_CHAMADA_MINUTOS = 120
# Cooldown de 2h só após chamada CONCLUÍDA com sucesso.
# Timeouts de cancelamento (não aplicam cooldown):
TIMEOUT_PRINT_EMS_SEGUNDOS = 600  # 10 min sem enviar print do /ems
# Tempo sem clicar em nenhum botão da sessão (após o OCR).
# Cada interação reinicia este contador. Valor alto para o doutor conferir
# nome a nome, editar, remover e adicionar sem perder a sessão.
TIMEOUT_INTERACAO_POS_OCR_SEGUNDOS = 3600  # 60 min sem interação
# Tempo máximo absoluto da sessão com lock (qualquer etapa).
# Protege contra chamada abandonada: outro doutor só consegue entrar depois.
TEMPO_MAXIMO_SESSAO_CHAMADA_MINUTOS = 90
LIMITE_FALTAS_PARA_ADVERTENCIA = 3
PENALIDADE_FALTA_MOEDAS = 1
BONUS_PRESENCA_CHAMADA = 1
BONUS_REALIZAR_CHAMADA = 1


# ---------------------------------------------------------------------------
# Baú do Hospital — limites por ciclo e políticas
# ---------------------------------------------------------------------------
# Ciclos de reset do contador (hora local TIMEZONE_LOCAL)
HORAS_RESET_CICLO_BAU = (0, 11, 17)

# Minutos para devolver após estourar limite 1/2
PRAZO_DEVOLUCAO_BAU_MINUTOS = 30

# Quantidade extra tolerada além do limite diário sem gerar alerta.
# Alerta só quando quantidade > limite_diario + TOLERANCIA_EXTRA_BAU
# Ex.: limite 1 → até 2 ok; alerta a partir de 3. Cristal 10 → até 11 ok; alerta 12+.
TOLERANCIA_EXTRA_BAU = 1

# 3 verbais → sobe para ADV 1 (notifica diretoria; decisão manual depois)
VERBAIS_PARA_ADV1_BAU = 3

# Limite 1: aviso + DM + timer (camada quantitativa)
LIMITES_BAU_CAMADA_1 = {
    "celular": 1,
    "roupas": 1,
    "mochila": 6,
    "cristal": 10,
    "radio": 1,
    "repairkit": 5,
    "mascara": 1,
}

# Limite 2: grave — alerta reforçado à diretoria (mesmo contador, outro patamar)
LIMITES_BAU_CAMADA_2 = {
    "celular": 25,
    "roupas": 25,
    "mochila": 30,
    "cristal": 30,
    "radio": 25,
    "repairkit": 20,
    "mascara": 20,
}

# Aliases normalizados (sem acento, lower) → chave canônica do limite
ALIASES_ITENS_BAU = {
    "celular": "celular",
    "celulares": "celular",
    "phone": "celular",
    "roupas": "roupas",
    "roupa": "roupas",
    "clothes": "roupas",
    "mochila": "mochila",
    "mochilas": "mochila",
    "bag": "mochila",
    "cristal": "cristal",
    "cristais": "cristal",
    "crystal": "cristal",
    "radio": "radio",
    "rádio": "radio",
    "radios": "radio",
    "repairkit": "repairkit",
    "repair kit": "repairkit",
    "kit reparo": "repairkit",
    "kitdereparo": "repairkit",
    "mascara": "mascara",
    "mascaras": "mascaras",
}

# ---------------------------------------------------------------------------
# Cursos (cargo Discord = comprovante de conclusão)
# valor_ingame: preço em R$ in-game (pagamento ao instrutor)
# moedas necessárias = ceil(valor_ingame / VALOR_MOEDA_INGAME)
# ---------------------------------------------------------------------------

CURSOS = {
    "resgate": {
        "nome": "Curso Resgate",
        "emoji": "🚑",
        "cargo_id": 1522578759037747361,
        "nivel": "1.0",
        "valor_ingame": 120_000,
        "pratico": True,
    },
    "arcanjo": {
        "nome": "Curso Arcanjo",
        "emoji": "🚁",
        "cargo_id": 1486368775543590994,
        "nivel": "1.0",
        "valor_ingame": 180_000,
        "pratico": True,
    },
    "mergulhador": {
        "nome": "Curso Mergulhador",
        "emoji": "🤿",
        "cargo_id": 1522578825513275482,
        "nivel": "1.0",
        "valor_ingame": 150_000,
        "pratico": True,
    },
    "alpinista": {
        "nome": "Curso Alpinista",
        "emoji": "🌄",
        "cargo_id": 1486368777728823468,
        "nivel": "1.0",
        "valor_ingame": 150_000,
        "pratico": True,
    },
    "paraquedista": {
        "nome": "Curso Paraquedista",
        "emoji": "🪂",
        "cargo_id": 1522578874234568814,
        "nivel": "1.0",
        "valor_ingame": 180_000,
        "pratico": True,
    },
    "arcanjo_2": {
        "nome": "Curso Arcanjo 2.0",
        "emoji": "🚁",
        "cargo_id": 1486368774582964394,
        "nivel": "2.0",
        "valor_ingame": 270_000,
        "pratico": True,
    },
    "mergulhador_2": {
        "nome": "Curso Mergulhador 2.0",
        "emoji": "🤿",
        "cargo_id": 1522578950323175424,
        "nivel": "2.0",
        "valor_ingame": 210_000,
        "pratico": True,
    },
    "alpinista_2": {
        "nome": "Curso Alpinista 2.0",
        "emoji": "🌄",
        "cargo_id": 1486368776646561834,
        "nivel": "2.0",
        "valor_ingame": 180_000,
        "pratico": True,
    },
    "paraquedista_2": {
        "nome": "Curso Paraquedista 2.0",
        "emoji": "🪂",
        "cargo_id": 1522578990743683203,
        "nivel": "2.0",
        "valor_ingame": 240_000,
        "pratico": True,
    },
    "doutor": {
        "nome": "Curso Doutor",
        "emoji": "🩺",
        "cargo_id": 1486368771860856882,
        "nivel": "funcao",
        "valor_ingame": 350_000,
        "pratico": False,
    },
    "psicologo": {
        "nome": "Curso Psicólogo",
        "emoji": "🧠",
        "cargo_id": 1486368771017805996,
        "nivel": "funcao",
        "valor_ingame": 400_000,
        "pratico": False,
    },
    "recrutador": {
        "nome": "Curso Recrutador",
        "emoji": "🫂",
        "cargo_id": 1522579072197197966,
        "nivel": "funcao",
        "valor_ingame": 550_000,
        "pratico": False,
    },
    "instrutor": {
        "nome": "Curso Instrutor",
        "emoji": "👨‍🏫",
        "cargo_id": 1522579028526239744,
        "nivel": "funcao",
        "valor_ingame": 750_000,
        "pratico": False,
    },
    "diretoria": {
        "nome": "Curso Diretoria",
        "emoji": "💎",
        "cargo_id": 1486368756606304388,
        "nivel": "diretoria",
        "valor_ingame": 2_000_000,
        "pratico": False,
    },
    "diretoria_geral": {
        "nome": "Curso Diretoria Geral",
        "emoji": "👑",
        "cargo_id": 1496189276365258873,
        "nivel": "diretoria",
        "valor_ingame": 0,
        "pratico": False,
    },
}

# ---------------------------------------------------------------------------
# Promoção — metas editáveis e trilhas
#
# METAS_POR_CARGO: ajuste livre de horas e produção de cada cargo.
# TRILHAS_PROMOCAO: caminhos formais (opção A) e combinações área → área.
# O Paramédico pode pedir qualquer área (opção B — primeira área).
# ---------------------------------------------------------------------------

# Fração mínima da meta para liberar pedido (0.9 = 90% conta como "próximo")
META_PROMOCAO_MARGEM = 0.9

# Cursos práticos reutilizados nas trilhas (só referência; edite em CURSOS)
CURSOS_PRATICOS_1 = ["arcanjo", "alpinista", "paraquedista", "mergulhador"]
CURSOS_PRATICOS_2 = ["arcanjo_2", "alpinista_2", "paraquedista_2", "mergulhador_2"]
CURSOS_DE_AREA = ["doutor", "psicologo", "recrutador", "instrutor"]
CURSOS_PARA_SUPERVISOR = (
    CURSOS_PRATICOS_1 + CURSOS_PRATICOS_2 + CURSOS_DE_AREA + ["diretoria"]
)

# Metas por cargo — edite aqui sem mexer na lógica do serviço.
# Os nomes CARGO_* estão definidos junto de CARGOS_HIERARQUIA (acima).
# Chaves de meta usadas pelo checklist:
#   segundos_minimos_plantao, meta_laudos, meta_recrutamentos,
#   meta_chamadas, meta_cursos_aplicados, exige_avaliacao_hp
METAS_POR_CARGO = {
    CARGO_ENFERMEIRO: {
        "segundos_minimos_plantao": 2 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 0,
        "meta_cursos_aplicados": 0,
        "exige_avaliacao_hp": False,
    },
    CARGO_PARAMEDICO: {
        "segundos_minimos_plantao": 6 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 0,
        "meta_cursos_aplicados": 0,
        "exige_avaliacao_hp": False,
    },
    CARGO_DOUTOR: {
        "segundos_minimos_plantao": 12 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 18,
        "meta_cursos_aplicados": 0,
        "exige_avaliacao_hp": False,
    },
    CARGO_PSICOLOGO: {
        "segundos_minimos_plantao": 16 * 3600,
        "meta_laudos": 12,
        "meta_recrutamentos": 0,
        "meta_chamadas": 10,
        "meta_cursos_aplicados": 0,
        "exige_avaliacao_hp": False,
    },
    CARGO_RECRUTADOR: {
        "segundos_minimos_plantao": 14 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 12,
        "meta_chamadas": 8,
        "meta_cursos_aplicados": 0,
        "exige_avaliacao_hp": False,
    },
    CARGO_INSTRUTOR: {
        "segundos_minimos_plantao": 20 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 8,
        "meta_cursos_aplicados": 10,
        "exige_avaliacao_hp": False,
    },
    CARGO_INSTRUTOR_RESGATE: {
        "segundos_minimos_plantao": 20 * 3600,
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 8,
        "meta_cursos_aplicados": 10,
        "exige_avaliacao_hp": False,
    },
    CARGO_SUPERVISOR: {
        "segundos_minimos_plantao": 40 * 3600,
        "meta_laudos": 20,
        "meta_recrutamentos": 15,
        "meta_chamadas": 20,
        "meta_cursos_aplicados": 12,
        "exige_avaliacao_hp": False,
    },
    CARGO_VICE_DIRETOR: {
        "segundos_minimos_plantao": 50 * 3600,
        "meta_laudos": 28,
        "meta_recrutamentos": 20,
        "meta_chamadas": 26,
        "meta_cursos_aplicados": 16,
        "exige_avaliacao_hp": False,
    },
    CARGO_DIRETOR: {
        "segundos_minimos_plantao": 60 * 3600,
        "meta_laudos": 36,
        "meta_recrutamentos": 26,
        "meta_chamadas": 32,
        "meta_cursos_aplicados": 22,
        "exige_avaliacao_hp": False,
    },
    CARGO_RESP_DOUTOR: {
        "segundos_minimos_plantao": 55 * 3600,
        "meta_laudos": 10,
        "meta_recrutamentos": 5,
        "meta_chamadas": 30,
        "meta_cursos_aplicados": 8,
        "exige_avaliacao_hp": False,
    },
    CARGO_RESP_PSICOLOGO: {
        "segundos_minimos_plantao": 55 * 3600,
        "meta_laudos": 30,
        "meta_recrutamentos": 5,
        "meta_chamadas": 12,
        "meta_cursos_aplicados": 8,
        "exige_avaliacao_hp": False,
    },
    CARGO_RESP_RECRUTAMENTO: {
        "segundos_minimos_plantao": 55 * 3600,
        "meta_laudos": 8,
        "meta_recrutamentos": 30,
        "meta_chamadas": 12,
        "meta_cursos_aplicados": 8,
        "exige_avaliacao_hp": False,
    },
    CARGO_RESP_INSTRUTOR: {
        "segundos_minimos_plantao": 55 * 3600,
        "meta_laudos": 8,
        "meta_recrutamentos": 8,
        "meta_chamadas": 12,
        "meta_cursos_aplicados": 28,
        "exige_avaliacao_hp": False,
    },
    CARGO_COORDENADOR: {
        "segundos_minimos_plantao": 70 * 3600,
        "meta_laudos": 40,
        "meta_recrutamentos": 30,
        "meta_chamadas": 36,
        "meta_cursos_aplicados": 26,
        "exige_avaliacao_hp": False,
    },
    CARGO_VICE_DIRETOR_GERAL: {
        "segundos_minimos_plantao": 80 * 3600,
        "meta_laudos": 45,
        "meta_recrutamentos": 35,
        "meta_chamadas": 40,
        "meta_cursos_aplicados": 30,
        "exige_avaliacao_hp": True,
    },
    CARGO_DIRETOR_GERAL: {
        "segundos_minimos_plantao": 90 * 3600,
        "meta_laudos": 50,
        "meta_recrutamentos": 40,
        "meta_chamadas": 45,
        "meta_cursos_aplicados": 35,
        "exige_avaliacao_hp": True,
    },
    CARGO_RESPONSAVEL_GERAL: {
        "segundos_minimos_plantao": 100 * 3600,
        "meta_laudos": 55,
        "meta_recrutamentos": 45,
        "meta_chamadas": 50,
        "meta_cursos_aplicados": 40,
        "exige_avaliacao_hp": True,
    },
}

# Horas de ETAPA (incrementais) para a PRIMEIRA área do Paramédico.
# O total exigido no banco = horas já "pagas" nos degraus anteriores + etapa.
# Ex.: Paramédico (2h) → Doutor (8h) exige 10h no total de plantão.
HORAS_PRIMEIRA_AREA = {
    CARGO_DOUTOR: 8 * 3600,
    CARGO_PSICOLOGO: 10 * 3600,
    CARGO_RECRUTADOR: 10 * 3600,
    CARGO_INSTRUTOR: 12 * 3600,
}

# Etapa Enfermeiro → Paramédico (base da carreira)
SEGUNDOS_ETAPA_ENFERMEIRO_PARAMEDICO = 2 * 3600

# Acumulado mínimo de plantão para "ter chegado" em cada cargo (via caminho
# canônico). Preenchido ao montar as trilhas.
SEGUNDOS_ACUMULADOS_ATE_CARGO: dict[str, int] = {
    CARGO_ENFERMEIRO: 0,
}


def _metas_do_cargo(nome_cargo: str) -> dict:
    """Copia as metas do cargo; devolve zeros se o cargo não estiver cadastrado."""
    base = METAS_POR_CARGO.get(nome_cargo) or {}
    return {
        "segundos_minimos_plantao": int(base.get("segundos_minimos_plantao") or 0),
        "meta_laudos": int(base.get("meta_laudos") or 0),
        "meta_recrutamentos": int(base.get("meta_recrutamentos") or 0),
        "meta_chamadas": int(base.get("meta_chamadas") or 0),
        "meta_cursos_aplicados": int(base.get("meta_cursos_aplicados") or 0),
        "exige_avaliacao_hp": bool(base.get("exige_avaliacao_hp") or False),
    }


def _registrar_acumulado(nome_cargo: str, segundos_totais: int) -> None:
    """Guarda o menor total de plantão conhecido para chegar naquele cargo."""
    atual = SEGUNDOS_ACUMULADOS_ATE_CARGO.get(nome_cargo)
    if atual is None or segundos_totais < atual:
        SEGUNDOS_ACUMULADOS_ATE_CARGO[nome_cargo] = int(segundos_totais)


def _montar_trilha(
    chave: str,
    rotulo: str,
    de_cargo: str,
    para_cargo: str,
    cursos: list[str],
    *,
    usar_metas_do_destino: bool = True,
    segundos_etapa: int | None = None,
    observacao: str = "",
    primeira_area: bool = False,
    exige_avaliacao_hp: bool | None = None,
) -> dict:
    """
    Monta uma trilha.

    ``segundos_etapa`` = horas DESTA promoção (o "cronômetro" do degrau).
    ``segundos_minimos_plantao`` = total no banco = acumulado até a origem
    + etapa. Assim Paramédico (2h) → Doutor (8h) exige 10h, não 8h.
    """
    metas = (
        _metas_do_cargo(para_cargo)
        if usar_metas_do_destino
        else {
            "segundos_minimos_plantao": 0,
            "meta_laudos": 0,
            "meta_recrutamentos": 0,
            "meta_chamadas": 0,
            "meta_cursos_aplicados": 0,
            "exige_avaliacao_hp": False,
        }
    )
    if segundos_etapa is None:
        # Sem override: a etapa é o plantão configurado no cargo de destino
        # (ou zero se não houver meta).
        segundos_etapa = int(metas.get("segundos_minimos_plantao") or 0)
    else:
        segundos_etapa = int(segundos_etapa)

    acumulado_origem = int(SEGUNDOS_ACUMULADOS_ATE_CARGO.get(de_cargo) or 0)
    segundos_totais = acumulado_origem + segundos_etapa
    metas["segundos_minimos_plantao"] = segundos_totais

    if exige_avaliacao_hp is not None:
        metas["exige_avaliacao_hp"] = bool(exige_avaliacao_hp)

    _registrar_acumulado(para_cargo, segundos_totais)

    return {
        "chave": chave,
        "rotulo": rotulo,
        "de_cargo": de_cargo,
        "para_cargo": para_cargo,
        "cursos_obrigatorios": list(cursos),
        "primeira_area": primeira_area,
        "observacao": observacao,
        "segundos_etapa": segundos_etapa,
        "segundos_acumulados_origem": acumulado_origem,
        **metas,
    }


# Áreas médicas (ordem de referência da trilha formal)
AREAS_MEDICAS = [
    (CARGO_DOUTOR, "doutor", ["doutor"]),
    (CARGO_PSICOLOGO, "psicologo", ["psicologo"]),
    (CARGO_RECRUTADOR, "recrutador", ["recrutador"]),
    (CARGO_INSTRUTOR, "instrutor", ["instrutor"]),
]

TRILHAS_PROMOCAO: list[dict] = []

# 1) Enfermeiro → Paramédico
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "enfermeiro_paramedico",
        "Enfermeiro → Paramédico",
        CARGO_ENFERMEIRO,
        CARGO_PARAMEDICO,
        ["resgate"],
        usar_metas_do_destino=False,
        segundos_etapa=SEGUNDOS_ETAPA_ENFERMEIRO_PARAMEDICO,
        observacao=(
            "Etapa de 2h de plantão (total 2h no banco). Boa conduta e sem adv."
        ),
    )
)

# 2) Paramédico → cada área (primeira área — opção B)
# Instrutor exige práticos 1.0 e 2.0; demais áreas só 1.0 + curso da área.
for cargo_area, chave_area, cursos_area in AREAS_MEDICAS:
    if chave_area == "instrutor":
        cursos_da_trilha = (
            list(CURSOS_PRATICOS_1) + list(CURSOS_PRATICOS_2) + list(cursos_area)
        )
        texto_cursos = "práticos 1.0 e 2.0 + curso Instrutor"
    else:
        cursos_da_trilha = list(CURSOS_PRATICOS_1) + list(cursos_area)
        texto_cursos = "práticos 1.0 + curso da área"
    etapa = int(HORAS_PRIMEIRA_AREA.get(cargo_area, 8 * 3600))
    TRILHAS_PROMOCAO.append(
        _montar_trilha(
            f"paramedico_{chave_area}",
            f"Paramédico → {cargo_area}",
            CARGO_PARAMEDICO,
            cargo_area,
            cursos_da_trilha,
            usar_metas_do_destino=False,
            segundos_etapa=etapa,
            primeira_area=True,
            observacao=(
                f"Primeira área: {texto_cursos}. "
                f"Etapa {etapa // 3600}h além das 2h de Paramédico "
                f"(total no banco = 2h + etapa). Sem metas de produção."
            ),
        )
    )

# 3) Área → área (todas as combinações, exceto a mesma)
# Metas de produção do cargo atual; plantão = acumulado na origem + etapa
# do cargo atual (METAS_POR_CARGO). Instrutor exige práticos 1.0 e 2.0.
for cargo_de, chave_de, _cursos_de in AREAS_MEDICAS:
    for cargo_para, chave_para, cursos_para in AREAS_MEDICAS:
        if cargo_de == cargo_para:
            continue
        if chave_para == "instrutor":
            cursos_da_trilha = (
                list(CURSOS_PRATICOS_1) + list(CURSOS_PRATICOS_2) + list(cursos_para)
            )
        else:
            cursos_da_trilha = list(CURSOS_PRATICOS_1) + list(cursos_para)
        etapa = int(_metas_do_cargo(cargo_de).get("segundos_minimos_plantao") or 0)
        TRILHAS_PROMOCAO.append(
            _montar_trilha(
                f"{chave_de}_{chave_para}",
                f"{cargo_de} → {cargo_para}",
                cargo_de,
                cargo_para,
                cursos_da_trilha,
                usar_metas_do_destino=False,
                segundos_etapa=etapa,
                observacao=(
                    "Meta e etapa de plantão do cargo atual. "
                    "O total no banco é acumulado + etapa "
                    "(não desconta o que já foi usado nas promoções anteriores)."
                ),
            )
        )
        # Metas de produção vêm do cargo de origem (já batidas para pedir)
        trilha_area = TRILHAS_PROMOCAO[-1]
        metas_origem = _metas_do_cargo(cargo_de)
        trilha_area["meta_laudos"] = metas_origem["meta_laudos"]
        trilha_area["meta_recrutamentos"] = metas_origem["meta_recrutamentos"]
        trilha_area["meta_chamadas"] = metas_origem["meta_chamadas"]
        trilha_area["meta_cursos_aplicados"] = metas_origem["meta_cursos_aplicados"]

# 4) Qualquer área → Supervisor (exige todas as quatro áreas na prática
#    via cursos; o serviço ainda confere cargos de área quando necessário)
for cargo_de, chave_de, _ in AREAS_MEDICAS:
    TRILHAS_PROMOCAO.append(
        _montar_trilha(
            f"{chave_de}_supervisor",
            f"{cargo_de} → Supervisor",
            cargo_de,
            CARGO_SUPERVISOR,
            list(CURSOS_PARA_SUPERVISOR),
            usar_metas_do_destino=True,
            observacao=(
                "Primeiro cargo da diretoria. Exige práticos 1.0 e 2.0, "
                "os quatro cursos de área, Curso Diretoria, horas e metas."
            ),
        )
    )

# 5) Diretoria interna
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "supervisor_vice_diretor",
        "Supervisor → Vice Diretor",
        CARGO_SUPERVISOR,
        CARGO_VICE_DIRETOR,
        [],
        observacao="Metas e horas do cargo. Sem curso novo.",
    )
)
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "vice_diretor_diretor",
        "Vice Diretor → Diretor",
        CARGO_VICE_DIRETOR,
        CARGO_DIRETOR,
        [],
        observacao="Metas e horas do cargo. Sem curso novo.",
    )
)

# 6) Diretoria → Responsável de área (destaque de produção)
for cargo_resp, chave_resp in (
    (CARGO_RESP_DOUTOR, "resp_doutor"),
    (CARGO_RESP_PSICOLOGO, "resp_psicologo"),
    (CARGO_RESP_RECRUTAMENTO, "resp_recrutamento"),
    (CARGO_RESP_INSTRUTOR, "resp_instrutor"),
):
    for cargo_de, chave_de in (
        (CARGO_SUPERVISOR, "supervisor"),
        (CARGO_VICE_DIRETOR, "vice_diretor"),
        (CARGO_DIRETOR, "diretor"),
    ):
        TRILHAS_PROMOCAO.append(
            _montar_trilha(
                f"{chave_de}_{chave_resp}",
                f"{cargo_de} → {cargo_resp}",
                cargo_de,
                cargo_resp,
                [],
                observacao=(
                    "Indicacao de destaque na área. Metas somadas da diretoria "
                    "e conferência do Responsável HP."
                ),
            )
        )

# 7) Responsável de área / Diretor → Coordenador
for cargo_de, chave_de in (
    (CARGO_DIRETOR, "diretor"),
    (CARGO_RESP_DOUTOR, "resp_doutor"),
    (CARGO_RESP_PSICOLOGO, "resp_psicologo"),
    (CARGO_RESP_RECRUTAMENTO, "resp_recrutamento"),
    (CARGO_RESP_INSTRUTOR, "resp_instrutor"),
):
    TRILHAS_PROMOCAO.append(
        _montar_trilha(
            f"{chave_de}_coordenador",
            f"{cargo_de} → Coordenador",
            cargo_de,
            CARGO_COORDENADOR,
            [],
            observacao="Metas e horas do cargo de Coordenador.",
        )
    )

# 8) Gerais (avaliacao do Responsavel HP)
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "coordenador_vice_geral",
        "Coordenador → Vice Diretor Geral",
        CARGO_COORDENADOR,
        CARGO_VICE_DIRETOR_GERAL,
        ["diretoria_geral"],
        exige_avaliacao_hp=True,
        observacao=(
            "Curso Diretoria Geral + horas + metas + avaliação do Responsável HP."
        ),
    )
)
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "vice_geral_diretor_geral",
        "Vice Diretor Geral → Diretor Geral",
        CARGO_VICE_DIRETOR_GERAL,
        CARGO_DIRETOR_GERAL,
        [],
        exige_avaliacao_hp=True,
        observacao="Metas + horas + avaliação do Responsável HP.",
    )
)
TRILHAS_PROMOCAO.append(
    _montar_trilha(
        "diretor_geral_resp_geral",
        "Diretor Geral → Responsável Geral",
        CARGO_DIRETOR_GERAL,
        CARGO_RESPONSAVEL_GERAL,
        [],
        exige_avaliacao_hp=True,
        observacao="Metas + horas + avaliação do Responsável HP.",
    )
)


# ---------------------------------------------------------------------------
# Apelidos em portugues (preferir estes em codigo novo)
#
# Os nomes originais em ingles continuam existindo logo acima porque batem com
# as chaves do arquivo .env e com imports que ja existem em dezenas de
# arquivos. Trocar aqueles nomes quebraria o deploy, e a regra 10 do projeto
# manda nunca quebrar o que funciona. Estes apelidos apontam para os mesmos
# valores e deixam o codigo novo 100% em portugues.
# ---------------------------------------------------------------------------

TOKEN_DO_BOT = DISCORD_TOKEN
ID_DO_SERVIDOR_PRINCIPAL = GUILD_ID
ID_DO_SERVIDOR_VALLEY = GUILD_ID_VALLEY
ENDERECO_DO_BANCO_DE_DADOS = DATABASE_URL
PASTA_DOS_BACKUPS = BACKUP_DIR
MAXIMO_DE_BACKUPS_POR_SERVIDOR = MAX_BACKUPS_PER_GUILD
HORAS_ENTRE_BACKUPS_AUTOMATICOS = AUTO_BACKUP_INTERVAL_HOURS
MINUTOS_ENTRE_BACKUPS_DO_BANCO = AUTO_BACKUP_DB_INTERVAL_MINUTES
NOMES_DOS_CARGOS_DE_ADMINISTRADOR = ADMIN_ROLE_NAMES
SEGUNDOS_PARA_EXPIRAR_CONFIRMACAO = CONFIRMATION_TIMEOUT
FUSO_HORARIO_LOCAL = TIMEZONE_LOCAL
