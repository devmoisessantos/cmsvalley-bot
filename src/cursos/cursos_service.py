"""Lógica de catálogo, posse, pacote e ciclo de vida do pedido de curso."""

from __future__ import annotations

import json
import logging

import discord
from sqlalchemy import select

from src.config import (
    CARGO_COORDENADOR,
    CARGO_ENFERMEIRO,
    CARGOS,
    CARGOS_HIERARQUIA,
    CURSOS,
    DIVISOR_REPASSE_CURSO,
    HORAS_ISENCAO_RESGATE,
    MOEDAS_DESCONTO_MAX_POR_PEDIDO,
    MOEDAS_DESCONTO_MAX_RESGATE_PARCIAL,
)
from src.database.conexao import async_session
from src.database.models import (
    EstadoPlantao,
    SolicitacaoCurso,
    agora,
)
from src.plantao.carteira_service import cotacao_moeda_do_membro
from src.plantao.plantao_permissoes import e_diretoria
from src.utils.error_handling import ignorar_falha_cosmetica
from src.utils.formatacao import formatar_reais

logger = logging.getLogger(__name__)


def listar_cursos_ordenados() -> list[tuple[str, dict]]:
    """Lista (chave, dados) — práticos 1.0, 2.0, depois função/diretoria."""
    ordem_nivel = {"1.0": 0, "2.0": 1, "funcao": 2, "diretoria": 3}
    itens = list(CURSOS.items())
    itens.sort(
        key=lambda par: (ordem_nivel.get(par[1].get("nivel"), 9), par[1]["nome"])
    )
    return itens


def obter_curso(chave: str) -> dict | None:
    """Retorna os dados do catálogo para a chave, ou `None` se ela não existir."""
    return CURSOS.get(chave)


def curso_e_pratico(chave: str) -> bool:
    """True se o curso está marcado como prático no catálogo."""
    dados = obter_curso(chave) or {}
    return bool(dados.get("pratico"))


def curso_e_area(chave: str) -> bool:
    """Cursos de função (doutor, psicólogo, recrutador, instrutor)."""
    return chave in ("doutor", "psicologo", "recrutador", "instrutor")


def curso_e_diretoria(chave: str) -> bool:
    """Cursos de diretoria (diretoria e diretoria geral)."""
    return chave in ("diretoria", "diretoria_geral")


def membro_e_instrutor(membro: discord.Member) -> bool:
    """True se o membro tem algum cargo cujo nome contenha 'instrutor'."""
    for cargo in membro.roles:
        nome = (cargo.name or "").lower()
        if "instrutor" in nome:
            return True
    return False


def membro_e_equipe_diretoria(membro: discord.Member) -> bool:
    """Equipe de diretoria (cargo de equipe ou cargos da lista CARGOS_DIRETORIA)."""
    if e_diretoria(membro):
        return True
    id_equipe = CARGOS.get("🌫️ EQUIPE・DIRETORIA")
    if id_equipe is None:
        return False
    return any(cargo.id == int(id_equipe) for cargo in membro.roles)


def membro_e_equipe_diretoria_geral(membro: discord.Member) -> bool:
    """Só a equipe de diretoria geral (ou administrador do Discord)."""
    if membro.guild_permissions.administrator:
        return True
    id_geral = CARGOS.get("💌 EQUIPE・DIRETORIA GERAL")
    if id_geral is None:
        return False
    return any(cargo.id == int(id_geral) for cargo in membro.roles)


# Cargo de responsável por cada curso de área (nome em CARGOS).
CARGO_RESPONSAVEL_POR_AREA = {
    "doutor": "👑・Responsável Doutor・🥼",
    "psicologo": "👑・Responsável Psicólogo・🧠",
    "recrutador": "👑・Responsável Recrutamento・🎯",
    "instrutor": "👑・Responsável Instrutor・🎓",
}

# Prazo em horas para aceitar agendamento e para decidir / pagar.
PRAZO_HORAS_CURSO = 48


def membro_e_responsavel_da_area(
    membro: discord.Member,
    chave: str,
) -> bool:
    """True se o membro tem o cargo de responsável daquela área."""
    nome_cargo = CARGO_RESPONSAVEL_POR_AREA.get(chave)
    if not nome_cargo:
        return False
    id_cargo = CARGOS.get(nome_cargo)
    if id_cargo is None:
        return False
    return any(cargo.id == int(id_cargo) for cargo in membro.roles)


def membro_pode_aplicar_chave(
    membro: discord.Member,
    chave: str,
) -> tuple[bool, str]:
    """
    Diz se o membro pode aplicar **um** curso.

    - Práticos 1.0 e 2.0: Instrutor ou Diretoria
    - Área: Equipe Diretoria **ou** responsável da área
    - Diretoria / Diretoria Geral: só Equipe Diretoria Geral
    """
    if membro.guild_permissions.administrator:
        return True, ""

    tem_diretoria_geral = membro_e_equipe_diretoria_geral(membro)
    tem_diretoria = membro_e_equipe_diretoria(membro) or tem_diretoria_geral
    tem_instrutor = membro_e_instrutor(membro)
    rotulo = rotulo_curso(chave)

    if curso_e_diretoria(chave):
        if tem_diretoria_geral:
            return True, ""
        return (
            False,
            f"O curso **{rotulo}** só pode ser aplicado pela "
            f"**Equipe Diretoria Geral**.",
        )

    if curso_e_area(chave):
        if tem_diretoria or membro_e_responsavel_da_area(membro, chave):
            return True, ""
        return (
            False,
            f"O curso **{rotulo}** só pode ser aplicado pela "
            f"**Equipe Diretoria** ou pelo **responsável da área**.",
        )

    if curso_e_pratico(chave):
        if tem_instrutor or tem_diretoria:
            return True, ""
        return (
            False,
            f"O curso **{rotulo}** só pode ser aplicado por "
            f"**Instrutor** ou **Diretoria**.",
        )

    # Chave desconhecida: só diretoria
    if tem_diretoria:
        return True, ""
    return (
        False,
        f"O curso **{rotulo}** exige **Equipe Diretoria**.",
    )


def separar_chaves_por_autorizacao(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[list[str], list[str]]:
    """
    Divide o pacote em (autorizadas, negadas) para este membro.

    Instrutor em pacote misto fica só com os práticos; área e diretoria
    vão para negadas e serão auto-reprovadas no aceite.
    """
    autorizadas: list[str] = []
    negadas: list[str] = []
    for chave in chaves:
        pode, _motivo = membro_pode_aplicar_chave(membro, chave)
        if pode:
            autorizadas.append(chave)
        else:
            negadas.append(chave)
    return autorizadas, negadas


def membro_pode_aplicar_chaves(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str]:
    """
    Diz se o membro pode **aceitar** o pedido.

    Basta ter autorização para **ao menos um** curso do pacote.
    Os demais (ex.: doutor num pacote misto aceito por instrutor) são
    auto-reprovados no aceite, sem exigir registro de pagamento.
    """
    if not chaves:
        return False, "Este pedido não tem cursos."

    if membro.guild_permissions.administrator:
        return True, ""

    autorizadas, negadas = separar_chaves_por_autorizacao(membro, chaves)
    if autorizadas:
        return True, ""

    # Nenhum curso do pacote é aplicável por este membro
    if negadas:
        primeiro = negadas[0]
        _pode, motivo = membro_pode_aplicar_chave(membro, primeiro)
        return False, motivo
    return False, "Apenas **Instrutor** ou **Diretoria** pode aceitar pedidos."


def canal_repasse_da_chave(chave: str) -> str:
    """
    Chave de CANAIS onde o comprovante desse curso deve ser postado.
    """
    if curso_e_pratico(chave):
        return "REGISTRAR_CURSO_PRATICOS"
    mapa = {
        "doutor": "REGISTRAR_CURSO_DOUTOR",
        "psicologo": "REGISTRAR_CURSO_PSICOLOGO",
        "recrutador": "REGISTRAR_CURSO_RECRUTADOR",
        "instrutor": "REGISTRAR_CURSO_INSTRUTOR",
        "diretoria": "REGISTRAR_CURSO_DIRETORIA",
        "diretoria_geral": "REGISTRAR_CURSO_DIRETORIA",
    }
    return mapa.get(chave, "REGISTRAR_CURSO_PRATICOS")


def calcular_repasse_hospital(valor_pago_ingame: int) -> int:
    """1/4 do valor pago in-game vai ao caixa do hospital."""
    if valor_pago_ingame <= 0:
        return 0
    divisor = int(DIVISOR_REPASSE_CURSO) or 4
    return int(valor_pago_ingame) // divisor


def valores_pagos_por_chave(
    chaves: list[str],
    valor_a_pagar_total: int,
) -> dict[str, int]:
    """
    Distribui o valor pago in-game entre os cursos na proporção do catálogo.
    """
    brutos: dict[str, int] = {}
    total_bruto = 0
    for chave in chaves:
        dados = obter_curso(chave) or {}
        bruto = int(dados.get("valor_ingame") or 0)
        brutos[chave] = bruto
        total_bruto += bruto
    if total_bruto <= 0 or valor_a_pagar_total <= 0:
        return {chave: 0 for chave in chaves}
    pagos: dict[str, int] = {}
    restante = int(valor_a_pagar_total)
    ordenadas = list(chaves)
    for indice, chave in enumerate(ordenadas):
        if indice == len(ordenadas) - 1:
            pagos[chave] = max(0, restante)
        else:
            fatia = int(valor_a_pagar_total * brutos[chave] / total_bruto)
            pagos[chave] = fatia
            restante -= fatia
    return pagos


def ler_repasses_feitos(registro: SolicitacaoCurso) -> dict:
    """Dicionário de grupos já registrados a partir de repasses_json."""
    bruto = getattr(registro, "repasses_json", None) or ""
    if not bruto:
        return {}
    try:
        dados = json.loads(bruto)
        if isinstance(dados, dict):
            return dados
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def montar_grupos_repasse(
    chaves: list[str],
    valor_a_pagar_total: int,
) -> list[dict]:
    """
    Grupos de comprovante:

    - um grupo ``praticos`` com todos os práticos do pedido
    - um grupo por curso de área (chave = nome do curso)
    """
    pagos = valores_pagos_por_chave(chaves, valor_a_pagar_total)
    praticos = [chave for chave in chaves if curso_e_pratico(chave)]
    areas = [chave for chave in chaves if not curso_e_pratico(chave)]
    grupos: list[dict] = []
    if praticos:
        valor_pago = sum(pagos.get(chave, 0) for chave in praticos)
        grupos.append(
            {
                "id": "praticos",
                "chaves": praticos,
                "valor_pago": valor_pago,
                "repasse": calcular_repasse_hospital(valor_pago),
                "canal_chave": "REGISTRAR_CURSO_PRATICOS",
                "rotulo": "Cursos práticos",
            }
        )
    for chave in areas:
        valor_pago = pagos.get(chave, 0)
        grupos.append(
            {
                "id": chave,
                "chaves": [chave],
                "valor_pago": valor_pago,
                "repasse": calcular_repasse_hospital(valor_pago),
                "canal_chave": canal_repasse_da_chave(chave),
                "rotulo": (obter_curso(chave) or {}).get("nome") or chave,
            }
        )
    return grupos


def proximo_grupo_repasse_pendente(
    registro: SolicitacaoCurso,
) -> dict | None:
    """Próximo grupo que ainda não teve comprovante registrado."""
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    grupos = montar_grupos_repasse(chaves, int(registro.valor_ingame or 0))
    feitos = ler_repasses_feitos(registro)
    for grupo in grupos:
        if not feitos.get(grupo["id"]):
            return grupo
    return None


def repasse_total_do_pedido(registro: SolicitacaoCurso) -> int:
    """Soma dos repasses de todos os grupos do pedido."""
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    grupos = montar_grupos_repasse(chaves, int(registro.valor_ingame or 0))
    return sum(int(grupo["repasse"]) for grupo in grupos)


def soma_valor_ingame(chaves: list[str]) -> int:
    """Soma somente cursos existentes para não cobrar chaves inválidas.

    Cada valor ausente é tratado como zero, permitindo reutilizar a função em
    seleções que ainda precisem ser validadas pela interface.
    """
    total = 0
    for chave in chaves:
        dados = obter_curso(chave)
        if dados:
            total += int(dados.get("valor_ingame") or 0)
    return total


def membro_e_enfermeiro(membro: discord.Member) -> bool:
    """True se o membro tem o cargo de Enfermeiro (isenção de Resgate)."""
    return any(cargo.name == CARGO_ENFERMEIRO for cargo in membro.roles)


def _cargo_mais_alto_hierarquia(membro: discord.Member) -> str | None:
    """Nome do cargo mais alto do membro na hierarquia do hospital."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return None


def membro_pode_solicitar_chave(
    membro: discord.Member,
    chave: str,
) -> tuple[bool, str]:
    """
    Diz se o membro pode **solicitar** um curso (lado do aluno).

    Regras:
    - Enfermeiro (cargo mais alto) → só **resgate**.
    - Paramédico e acima (abaixo de Coordenador) → práticos, função e
      diretoria; **exceto** diretoria_geral.
    - Coordenador e acima → todos os cursos, inclusive diretoria_geral.
    - Administrador do Discord → sempre autorizado.
    """
    if membro.guild_permissions.administrator:
        return True, ""

    if chave not in CURSOS:
        return False, "Curso inválido."

    cargo = _cargo_mais_alto_hierarquia(membro)

    # Sem cargo na hierarquia ou só Enfermeiro: apenas Resgate.
    if cargo is None or cargo == CARGO_ENFERMEIRO:
        if chave == "resgate":
            return True, ""
        return (
            False,
            "Você não está autorizado a realizar esse(s) curso(s).",
        )

    # Coordenador e cargos acima na lista (índice menor = mais alto).
    try:
        indice_cargo = CARGOS_HIERARQUIA.index(cargo)
        indice_coordenador = CARGOS_HIERARQUIA.index(CARGO_COORDENADOR)
    except ValueError:
        # Cargo fora da lista esperada: trata como enfermeiro.
        if chave == "resgate":
            return True, ""
        return (
            False,
            "Você não está autorizado a realizar esse(s) curso(s).",
        )

    if indice_cargo <= indice_coordenador:
        return True, ""

    # Paramédico até abaixo de Coordenador: tudo menos diretoria_geral.
    if chave == "diretoria_geral":
        return (
            False,
            "Você não está autorizado a realizar esse(s) curso(s).",
        )
    return True, ""


def membro_pode_solicitar_chaves(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str, list[str]]:
    """
    Valida um pacote de cursos para solicitação.

    Retorna (ok, mensagem_erro, chaves_negadas).
    Se qualquer chave for negada, ok=False e a mensagem é a padrão.
    """
    negadas: list[str] = []
    for chave in chaves:
        pode, _ = membro_pode_solicitar_chave(membro, chave)
        if not pode:
            negadas.append(chave)
    if negadas:
        return (
            False,
            "Você não está autorizado a realizar esse(s) curso(s).",
            negadas,
        )
    return True, "", []


async def segundos_plantao_para_isencao_resgate(
    discord_id: int,
) -> int:
    """
    Tempo total de plantão do membro para isenção do Resgate (≥ 6h).

    Soma o histórico completo (todos os logs fechados) e o trecho ainda
    em call, se o toggle estiver ligado. Não usa só o ciclo semanal —
    o Enfermeiro precisa das horas totais acumuladas.
    """
    from src.plantao.ranking_plantao_service import (
        _somar_segundos_em_call_ao_vivo,
        obter_segundos_plantao_totais,
    )

    segundos_historico = await obter_segundos_plantao_totais(discord_id)
    ao_vivo = await _somar_segundos_em_call_ao_vivo()
    segundos_abertos = int(ao_vivo.get(int(discord_id), 0))
    return max(0, segundos_historico + segundos_abertos)


# Nome antigo mantido para quem ainda importa; delega ao total histórico.
async def segundos_plantao_ciclo_atual(
    discord_id: int,
    *,
    guild: discord.Guild | None = None,
) -> int:
    """
    Compatibilidade: agora devolve o total histórico + call aberta.

    O parâmetro guild é ignorado de propósito — a isenção do Resgate
    não filtra por ranking.
    """
    _ = guild
    return await segundos_plantao_para_isencao_resgate(discord_id)


async def calcular_cobranca_pacote(
    membro: discord.Member,
    chaves: list[str],
    *,
    moedas_desconto_desejadas: int = 0,
) -> dict:
    """
    Calcula valor IN_GAME e desconto em moedas para o pacote.

    Regras:
    - Pagamento base sempre em dinheiro in-game.
    - Moedas só abatem (nunca pagam o curso sozinhas).
    - Resgate: enfermeiro com ≥ 6h no total histórico de plantão
      → grátis (sem desconto). Com < 6h → desconto máximo de 5 moedas.
    - Demais cursos / pacotes: desconto máximo de 10 moedas.
    - Abatimento em R$ = moedas × cotação do aluno, sem passar do total.
    """
    chaves_validas = [chave for chave in chaves if obter_curso(chave)]
    cotacao = cotacao_moeda_do_membro(membro)
    valor_bruto = soma_valor_ingame(chaves_validas)

    so_resgate = chaves_validas == ["resgate"]
    isento = False
    motivo_isencao = ""
    teto_moedas = MOEDAS_DESCONTO_MAX_POR_PEDIDO

    if so_resgate and membro_e_enfermeiro(membro):
        # Total histórico (logs fechados) + tempo ainda em call.
        segundos = await segundos_plantao_para_isencao_resgate(membro.id)
        horas = segundos / 3600.0
        if horas >= HORAS_ISENCAO_RESGATE:
            isento = True
            motivo_isencao = (
                f"Enfermeiro com {horas:.1f}h de plantão no total "
                f"(mínimo {HORAS_ISENCAO_RESGATE}h)."
            )
            valor_bruto = 0
            teto_moedas = 0
        else:
            teto_moedas = MOEDAS_DESCONTO_MAX_RESGATE_PARCIAL

    if isento:
        return {
            "chaves": chaves_validas,
            "valor_bruto": 0,
            "valor_a_pagar_ingame": 0,
            "moedas_desconto": 0,
            "desconto_em_reais": 0,
            "cotacao": cotacao,
            "teto_moedas": 0,
            "isento": True,
            "motivo_isencao": motivo_isencao,
            "forma": "GRATUITO",
        }

    desejadas = max(0, int(moedas_desconto_desejadas))
    moedas_usadas = min(desejadas, teto_moedas)

    # Desconto real pelos lotes (menor cotação → maior), não pela cotação atual
    from src.plantao.carteira_service import simular_valor_debito

    if moedas_usadas > 0 and valor_bruto > 0:
        # Ajusta quantidade para não passar do valor do curso
        while moedas_usadas > 0:
            desconto_tentativa = await simular_valor_debito(
                membro.id, moedas_usadas
            )
            if desconto_tentativa <= valor_bruto:
                break
            moedas_usadas -= 1
        desconto_reais = await simular_valor_debito(membro.id, moedas_usadas)
    else:
        desconto_reais = 0

    valor_a_pagar = max(0, valor_bruto - desconto_reais)

    return {
        "chaves": chaves_validas,
        "valor_bruto": valor_bruto,
        "valor_a_pagar_ingame": valor_a_pagar,
        "moedas_desconto": moedas_usadas,
        "desconto_em_reais": desconto_reais,
        "cotacao": cotacao,
        "teto_moedas": teto_moedas,
        "isento": False,
        "motivo_isencao": "",
        "forma": "IN_GAME",
    }


# Compatibilidade com trechos antigos que ainda importam estes nomes.
def moedas_necessarias_para_valor(valor_ingame: int) -> int:
    """Legado: não use para cobrar curso. Preferir calcular_cobranca_pacote."""
    import math

    from src.config import VALOR_MOEDA_INGAME

    if valor_ingame <= 0:
        return 0
    return max(1, math.ceil(valor_ingame / VALOR_MOEDA_INGAME))


def moedas_necessarias_para_curso(chave: str) -> int:
    """Legado — mantido para imports antigos."""
    dados = obter_curso(chave)
    if not dados:
        return 0
    return moedas_necessarias_para_valor(int(dados.get("valor_ingame") or 0))


def moedas_necessarias_para_pacote(chaves: list[str]) -> int:
    """Legado — teto de desconto, não preço integral em moedas."""
    return MOEDAS_DESCONTO_MAX_POR_PEDIDO


def membro_tem_curso(membro: discord.Member, chave: str) -> bool:
    """Confere o cargo do catálogo para evitar vender curso já concluído.

    Retorna falso para uma chave ausente, pois sem dados de cargo não é seguro
    considerar que o membro possui a habilitação correspondente.
    """
    dados = obter_curso(chave)
    if not dados:
        return False
    cargo_id = int(dados["cargo_id"])
    return any(cargo.id == cargo_id for cargo in membro.roles)


def listar_cursos_que_faltam(
    membro: discord.Member,
    chaves: list[str],
) -> list[str]:
    """Filtra as chaves que ainda não correspondem a cargos do membro."""
    return [chave for chave in chaves if not membro_tem_curso(membro, chave)]


def rotulo_curso(chave: str) -> str:
    """Gera o rótulo amigável do catálogo e preserva a chave se ela for desconhecida."""
    dados = obter_curso(chave)
    if not dados:
        return chave
    return f"{dados.get('emoji', '')} {dados['nome']}".strip()


def menção_cargo_curso(chave: str) -> str:
    """Menciona o cargo do curso ou mostra a chave para não ocultar dados inválidos."""
    dados = obter_curso(chave)
    if not dados:
        return f"`{chave}`"
    return f"<@&{int(dados['cargo_id'])}>"


def parse_chaves_json(texto: str | None, chave_unica: str | None = None) -> list[str]:
    """Lê as chaves de um pacote e oferece suporte aos pedidos antigos.

    Quando o JSON é inválido ou inexistente, usa a chave individual legada,
    exceto o marcador `pacote`, que não representa um curso real.
    """
    if texto:
        try:
            lista = json.loads(texto)
            if isinstance(lista, list):
                return [str(item) for item in lista]
        except json.JSONDecodeError as erro_em_parse_chaves_json:
            # Enfeite que falhou: ler as chaves em JSON.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_parse_chaves_json,
                o_que_falhou="ler as chaves em JSON",
            )
    if chave_unica and chave_unica != "pacote":
        return [chave_unica]
    return []


async def consultar_saldo_moedas(discord_id: int) -> int:
    """Obtém o saldo de plantão, retornando zero para quem ainda não tem estado.

    Esse retorno seguro evita que uma solicitação de curso dependa de um
    registro prévio e impede que a ausência de saldo seja interpretada como erro.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return 0
        return int(estado.saldo_moedas or 0)


async def debitar_moedas_curso(
    discord_id: int,
    quantidade: int,
) -> tuple[bool, int, str]:
    """
    Debita moedas pelos lotes (menor cotação → maior).

    Retorna (ok, saldo_restante, mensagem_erro).
    """
    if quantidade <= 0:
        return True, await consultar_saldo_moedas(discord_id), ""

    from src.plantao.carteira_service import debitar_lotes

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return False, 0, "Você ainda não tem saldo de plantão registrado."
        saldo = int(estado.saldo_moedas or 0)
        if saldo < quantidade:
            return (
                False,
                saldo,
                (
                    f"Saldo insuficiente. Você tem **{saldo}** moeda(s) "
                    f"e precisa de **{quantidade}**."
                ),
            )
        ok_lotes, _valor = await debitar_lotes(
            sessao, discord_id, quantidade, saldo
        )
        if not ok_lotes:
            return (
                False,
                saldo,
                (
                    f"Saldo insuficiente. Você tem **{saldo}** moeda(s) "
                    f"e precisa de **{quantidade}**."
                ),
            )
        estado.saldo_moedas = saldo - quantidade
        await sessao.commit()
        return True, int(estado.saldo_moedas), ""


async def creditar_moedas_instrutor(
    discord_id: int,
    quantidade: int,
    cotacao: int | None = None,
) -> int:
    """
    Credita moedas ao instrutor no lote da cotação informada (ou mínima).

    Retorna saldo final.
    """
    if quantidade <= 0:
        return await consultar_saldo_moedas(discord_id)

    from src.config import VALOR_MOEDA_INGAME
    from src.plantao.carteira_service import creditar_lotes

    cotacao_usar = int(cotacao) if cotacao and cotacao > 0 else int(VALOR_MOEDA_INGAME)

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=discord_id, saldo_moedas=0)
            sessao.add(estado)
            await sessao.flush()
        await creditar_lotes(sessao, discord_id, quantidade, cotacao_usar)
        estado.saldo_moedas = int(estado.saldo_moedas or 0) + quantidade
        await sessao.commit()
        return int(estado.saldo_moedas)


async def registrar_solicitacao_pacote(
    *,
    discord_id: int,
    chaves: list[str],
    forma_pagamento: str,
    moedas_debitadas: int,
    observacao_aluno: str | None,
    valor_a_pagar_ingame: int | None = None,
    cotacao_moeda: int = 0,
) -> SolicitacaoCurso:
    """Cria no banco um pedido agendado para um curso ou pacote.

    ``valor_a_pagar_ingame`` já vem com desconto de moedas aplicado.
    Se não for informado, usa a soma bruta do catálogo.
    """
    valor_bruto = soma_valor_ingame(chaves)
    valor_gravar = (
        int(valor_a_pagar_ingame) if valor_a_pagar_ingame is not None else valor_bruto
    )
    chave_resumo = chaves[0] if len(chaves) == 1 else "pacote"
    async with async_session() as sessao:
        registro = SolicitacaoCurso(
            discord_id=discord_id,
            chave_curso=chave_resumo,
            chaves_cursos_json=json.dumps(chaves, ensure_ascii=False),
            valor_ingame=valor_gravar,
            moedas_debitadas=moedas_debitadas,
            cotacao_moeda=int(cotacao_moeda or 0),
            forma_pagamento=forma_pagamento,
            repasse_registrado=False,
            status="AGENDADO",
            observacao_aluno=(observacao_aluno or "").strip() or None,
            criado_em=agora(),
            atualizado_em=agora(),
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def obter_solicitacao_curso(solicitacao_id: int) -> SolicitacaoCurso | None:
    """Busca um pedido pelo identificador, retornando `None` se ele não existir."""
    try:
        identificador = int(solicitacao_id)
    except (TypeError, ValueError):
        return None
    if identificador <= 0:
        return None

    async with async_session() as sessao:
        # session.get usa a PK direto (mais estável que WHERE em alguns pools)
        registro = await sessao.get(SolicitacaoCurso, identificador)
        if registro is not None:
            return registro

        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == identificador)
        )
        return resultado.scalar_one_or_none()


async def obter_solicitacao_por_mensagem(
    mensagem_id: int,
) -> SolicitacaoCurso | None:
    """
    Busca pedido pela mensagem publicada no Discord.

    Serve de rede de segurança quando o custom_id do botão ficou
    dessincronizado do banco, mas a mensagem ainda está vinculada
    em mensagem_id.
    """
    if not mensagem_id:
        return None
    try:
        id_mensagem = int(mensagem_id)
    except (TypeError, ValueError):
        return None

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.mensagem_id == id_mensagem)
        )
        return resultado.scalar_one_or_none()


async def resolver_solicitacao_curso(
    solicitacao_id: int,
    *,
    mensagem_id: int | None = None,
) -> SolicitacaoCurso | None:
    """
    Resolve o pedido pelo id do botão; se não achar, tenta pela mensagem.

    Assim um card antigo com id errado no custom_id ainda pode
    funcionar se mensagem_id estiver gravado no banco.
    """
    import logging

    registrador_local = logging.getLogger(__name__)

    try:
        identificador = int(solicitacao_id)
    except (TypeError, ValueError):
        identificador = 0

    registro = await obter_solicitacao_curso(identificador)
    if registro is not None:
        return registro

    if mensagem_id is not None:
        registro = await obter_solicitacao_por_mensagem(mensagem_id)
        if registro is not None:
            registrador_local.warning(
                "Pedido de curso: custom_id apontava para #%s (sumiu), "
                "mas a mensagem %s está ligada ao pedido #%s — usando este.",
                identificador,
                mensagem_id,
                registro.id,
            )
            return registro

    registrador_local.warning(
        "Pedido de curso não encontrado: id=%s mensagem_id=%s",
        identificador,
        mensagem_id,
    )
    return None


async def listar_solicitacoes_por_status(
    *status_lista: str,
) -> list[SolicitacaoCurso]:
    """Lista pedidos com um dos status informados, do mais antigo ao mais novo.

    Usado por comandos admin para republicar a fila de agendamento ou
    de decisão sem depender das mensagens já publicadas no Discord.
    """
    if not status_lista:
        return []
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso)
            .where(SolicitacaoCurso.status.in_(status_lista))
            .order_by(SolicitacaoCurso.id.asc())
        )
        return list(resultado.scalars().all())


async def buscar_pedido_aberto(discord_id: int) -> SolicitacaoCurso | None:
    """Pedido ainda em andamento (agendado ou aceito) do aluno."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso)
            .where(SolicitacaoCurso.discord_id == discord_id)
            .where(SolicitacaoCurso.status.in_(("AGENDADO", "ACEITO")))
            .order_by(SolicitacaoCurso.id.asc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def mesclar_cursos_no_pedido(
    *,
    solicitacao_id: int,
    novas_chaves: list[str],
    forma_pagamento: str,
    moedas_extra: int,
    observacao_aluno: str | None,
    cotacao_moeda: int = 0,
) -> SolicitacaoCurso | None:
    """
    Acrescenta cursos a um pedido aberto.
    Só adiciona chaves que ainda não estavam no pacote.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("AGENDADO", "ACEITO"):
            return registro

        atuais = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
        unidas: list[str] = []
        for chave in atuais + list(novas_chaves):
            if chave not in unidas:
                unidas.append(chave)

        registro.chaves_cursos_json = json.dumps(unidas, ensure_ascii=False)
        registro.chave_curso = unidas[0] if len(unidas) == 1 else "pacote"
        # Valor a pagar: bruto do pacote menos desconto já acumulado
        valor_bruto = soma_valor_ingame(unidas)
        registro.moedas_debitadas = int(registro.moedas_debitadas or 0) + int(
            moedas_extra
        )
        if cotacao_moeda > 0 and int(getattr(registro, "cotacao_moeda", 0) or 0) == 0:
            registro.cotacao_moeda = int(cotacao_moeda)
        cotacao = int(getattr(registro, "cotacao_moeda", 0) or 0)
        desconto = int(registro.moedas_debitadas or 0) * cotacao
        registro.valor_ingame = max(0, valor_bruto - desconto)
        if int(registro.moedas_debitadas or 0) > 0:
            if forma_pagamento == "MOEDAS":
                registro.forma_pagamento = "MOEDAS"
            else:
                registro.forma_pagamento = "IN_GAME_COM_DESCONTO"
        elif forma_pagamento:
            registro.forma_pagamento = forma_pagamento

        if observacao_aluno and observacao_aluno.strip():
            anterior = (registro.observacao_aluno or "").strip()
            nova = observacao_aluno.strip()
            if anterior and nova not in anterior:
                registro.observacao_aluno = f"{anterior}\n---\n{nova}"
            elif not anterior:
                registro.observacao_aluno = nova

        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def redefinir_chaves_do_pedido(
    *,
    solicitacao_id: int,
    chaves_finais: list[str],
    status_permitidos: tuple[str, ...] = ("AGENDADO",),
) -> SolicitacaoCurso | None:
    """
    Troca a lista de cursos de um pedido aberto.

    Recalcula o valor in-game com o desconto de moedas já debitado.
    Se a lista ficar vazia e o status for AGENDADO, cancela o pedido.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in status_permitidos:
            return registro

        unidas: list[str] = []
        for chave in chaves_finais:
            if chave not in unidas:
                unidas.append(chave)

        if not unidas:
            if registro.status == "AGENDADO":
                registro.status = "CANCELADO"
            registro.chaves_cursos_json = json.dumps([], ensure_ascii=False)
            registro.chave_curso = "pacote"
            registro.valor_ingame = 0
            registro.atualizado_em = agora()
            await sessao.commit()
            await sessao.refresh(registro)
            return registro

        registro.chaves_cursos_json = json.dumps(unidas, ensure_ascii=False)
        registro.chave_curso = unidas[0] if len(unidas) == 1 else "pacote"
        valor_bruto = soma_valor_ingame(unidas)
        cotacao = int(getattr(registro, "cotacao_moeda", 0) or 0)
        desconto = int(registro.moedas_debitadas or 0) * cotacao
        registro.valor_ingame = max(0, valor_bruto - desconto)
        if int(registro.moedas_debitadas or 0) > 0:
            registro.forma_pagamento = "IN_GAME_COM_DESCONTO"
        elif (registro.forma_pagamento or "") == "GRATUITO":
            registro.forma_pagamento = "GRATUITO"
        else:
            registro.forma_pagamento = "IN_GAME"
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def listar_pedidos_expirados(
    *,
    prazo_horas: int = PRAZO_HORAS_CURSO,
) -> list[SolicitacaoCurso]:
    """
    Pedidos AGENDADO ou ACEITO cujo prazo de 48h já passou.

    - AGENDADO: conta a partir de ``criado_em`` (aceite).
    - ACEITO: conta a partir de ``atualizado_em`` (decisão / pagamento).
    """
    from datetime import timedelta

    limite = agora() - timedelta(hours=int(prazo_horas))
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(
                SolicitacaoCurso.status.in_(("AGENDADO", "ACEITO"))
            )
        )
        registros = list(resultado.scalars().all())

    expirados: list[SolicitacaoCurso] = []
    for registro in registros:
        if registro.status == "AGENDADO":
            referencia = registro.criado_em
        else:
            referencia = registro.atualizado_em or registro.criado_em
        if referencia is None:
            continue
        # Normaliza para aware se o driver devolver naive
        if referencia.tzinfo is None:
            from datetime import timezone as fuso

            referencia = referencia.replace(tzinfo=fuso.utc)
        if referencia <= limite:
            expirados.append(registro)
    return expirados


async def anular_e_apagar_pedido(solicitacao_id: int) -> SolicitacaoCurso | None:
    """
    Lê o pedido, devolve os dados e **apaga** a linha do banco.

    Quem chama deve apagar a mensagem do Discord usando
    ``mensagem_canal_id`` / ``mensagem_id`` antes ou depois.
    Moedas de desconto, se houver, são devolvidas ao aluno.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None

        # Cópia dos campos que o caller precisa (mensagem, moedas, aluno)
        class _CopiaPedido:
            pass

        copia = _CopiaPedido()
        copia.id = registro.id
        copia.discord_id = registro.discord_id
        copia.status = registro.status
        copia.moedas_debitadas = int(registro.moedas_debitadas or 0)
        copia.mensagem_canal_id = registro.mensagem_canal_id
        copia.mensagem_id = registro.mensagem_id
        copia.chaves_cursos_json = registro.chaves_cursos_json
        copia.chave_curso = registro.chave_curso

        await sessao.delete(registro)
        await sessao.commit()
        return copia  # type: ignore[return-value]


async def decidir_cursos_parciais(
    *,
    solicitacao_id: int,
    chaves_aprovadas: list[str],
    chaves_reprovadas: list[str],
    instrutor_id: int,
    observacao_decisao: str | None = None,
) -> SolicitacaoCurso | None:
    """
    Fecha o pedido com listas de aprovados/reprovados.
    Status final: APROVADO se houver ao menos um aprovado; senão REPROVADO.

    ``observacao_decisao`` é a nota da aprovação/reprovação (não reutiliza
    a observação da aceitação do agendamento).
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("ACEITO", "AGENDADO"):
            return registro

        linhas_aprovados = (
            "\n".join(f"> {chave}\n" for chave in chaves_aprovadas)
            if chaves_aprovadas
            else "> ———"
        )

        linhas_reprovados = (
            "\n".join(f"> {chave}\n" for chave in chaves_reprovadas)
            if chaves_reprovadas
            else "> ———"
        )

        resumo = (
            f"**📝 Observação da decisão:**\n> {nota}\n\n"
            if (nota := (observacao_decisao or "").strip())
            else ""
        )

        resumo += (
            f"**✅ Aprovados:**\n"
            f"{linhas_aprovados}\n\n"
            f"**❌ Reprovados:**\n"
            f"{linhas_reprovados}"
        )
        # Substitui pela decisão final — não mantém só a obs. de aceitação
        registro.observacao_instrutor = resumo[:700]
        registro.aplicado_por = instrutor_id
        registro.status = "APROVADO" if chaves_aprovadas else "REPROVADO"
        if chaves_aprovadas and chaves_reprovadas:
            registro.status = "APROVADO"
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_solicitacao_curso(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    """Vincula ao pedido a mensagem publicada para decisão dos instrutores.

    A gravação no banco permite editar ou localizar o mesmo card depois, sem
    enviar respostas duplicadas. Não faz nada se o pedido tiver sido removido.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = agora()
        await sessao.commit()


async def limpar_mensagem_solicitacao_curso(solicitacao_id: int) -> None:
    """Remove do banco a referência da mensagem do card de agendamento.

    Usado quando o card é apagado do Discord (aceito, recusado ou
    republicado mais abaixo no canal). Assim não tentamos editar uma
    mensagem que já não existe.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.mensagem_canal_id = None
        registro.mensagem_id = None
        registro.atualizado_em = agora()
        await sessao.commit()


async def aceitar_agendamento(
    *,
    solicitacao_id: int,
    instrutor_id: int,
    observacao_instrutor: str | None,
) -> SolicitacaoCurso | None:
    """Reserva um pedido agendado para o instrutor que o aceitou.

    Só muda pedidos ainda agendados, protegendo contra dois instrutores
    aceitarem o mesmo aluno ao mesmo tempo. Salva a observação opcional e
    devolve o registro resultante ou `None` quando ele não existe.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None or registro.status != "AGENDADO":
            return registro
        registro.status = "ACEITO"
        registro.instrutor_id = instrutor_id
        registro.observacao_instrutor = (observacao_instrutor or "").strip() or None
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def recusar_agendamento(
    *,
    solicitacao_id: int,
    instrutor_id: int,
    motivo: str | None,
) -> SolicitacaoCurso | None:
    """
    Recusa um pedido ainda em AGENDADO (erro do aluno, data inválida, etc.).

    Status vira CANCELADO. Moedas de desconto, se houver, são devolvidas
    ao aluno fora desta função (quem chama decide).
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status != "AGENDADO":
            return registro
        registro.status = "CANCELADO"
        registro.instrutor_id = instrutor_id
        registro.aplicado_por = instrutor_id
        texto_motivo = (motivo or "").strip()
        if texto_motivo:
            registro.observacao_instrutor = texto_motivo[:500]
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def cancelar_pedido_aceito(
    *,
    solicitacao_id: int,
    staff_id: int,
    motivo: str | None,
) -> SolicitacaoCurso | None:
    """
    Cancela um pedido em ACEITO (aluno desistiu, saiu, exonerado, etc.).

    Status vira CANCELADO. Quem chama deve retirar cargos de material e
    devolver moedas de desconto. Convive com o prazo automático de 48h:
    cancela na hora em vez de esperar a varredura anular o pedido.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status != "ACEITO":
            return registro
        registro.status = "CANCELADO"
        registro.aplicado_por = staff_id
        texto_motivo = (motivo or "").strip()
        if texto_motivo:
            registro.observacao_instrutor = texto_motivo[:500]
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def decidir_curso(
    *,
    solicitacao_id: int,
    aprovado: bool,
    instrutor_id: int,
    motivo: str | None = None,
) -> SolicitacaoCurso | None:
    """Registra a decisão final do instrutor para um pedido aberto.

    Atualiza o banco somente quando o pedido está agendado ou aceito, evitando
    rever decisões já concluídas. Em reprovações, conserva um motivo limitado
    ao tamanho aceito pelo campo de observação.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("ACEITO", "AGENDADO"):
            return registro
        registro.status = "APROVADO" if aprovado else "REPROVADO"
        registro.aplicado_por = instrutor_id
        if not aprovado and motivo:
            registro.observacao_instrutor = (motivo or "")[:500]
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def conceder_cargos_dos_cursos(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str]:
    """Concede ao aluno os cargos dos cursos aprovados no Discord.

    Ignora chaves inexistentes e cargos que ele já possui, evitando operações
    redundantes. Retorna um indicador e uma mensagem de diagnóstico em vez de
    propagar erros de permissão ou comunicação do Discord.
    """
    guilda = membro.guild
    cargos_para_adicionar: list[discord.Role] = []
    for chave in chaves:
        dados = obter_curso(chave)
        if not dados:
            continue
        cargo = guilda.get_role(int(dados["cargo_id"]))
        if cargo is None:
            return False, f"Cargo do curso `{chave}` não encontrado na guilda."
        if cargo not in membro.roles:
            cargos_para_adicionar.append(cargo)
    if not cargos_para_adicionar:
        return True, "Aluno já possuía os cargos dos cursos."
    try:
        await membro.add_roles(
            *cargos_para_adicionar,
            reason="Curso aprovado pelo instrutor",
        )
    except discord.Forbidden:
        return False, "Sem permissão para conceder cargos de curso."
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao conceder cargos: {erro}"
    return True, "Cargos de curso concedidos."


def _cargos_cursando_das_chaves(
    guilda: discord.Guild,
    chaves: list[str],
) -> tuple[list[discord.Role], str | None]:
    """
    Resolve os cargos de material (cursando) das chaves pedidas.

    Devolve a lista de cargos válidos e, se algum id estiver faltando na
    guilda, uma mensagem de diagnóstico. Chaves sem cargo_cursando_id no
    catálogo são ignoradas em silêncio.
    """
    cargos: list[discord.Role] = []
    for chave in chaves:
        dados = obter_curso(chave)
        if not dados:
            continue
        cargo_cursando_id = dados.get("cargo_cursando_id")
        if not cargo_cursando_id:
            continue
        cargo = guilda.get_role(int(cargo_cursando_id))
        if cargo is None:
            return [], (
                f"Cargo de material do curso `{chave}` "
                f"(id {cargo_cursando_id}) não encontrado na guilda."
            )
        if cargo not in cargos:
            cargos.append(cargo)
    return cargos, None


async def conceder_cargos_cursando(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str]:
    """Libera o material só dos cursos aceitos, pelo cargo de cada curso.

    Antes existia um único cargo de cursante que abria todos os canais.
    Agora cada curso tem o próprio cargo e o aluno só vê o material
    correspondente ao pedido aceito.
    """
    cargos, erro = _cargos_cursando_das_chaves(membro.guild, chaves)
    if erro:
        return False, erro
    cargos_para_adicionar = [cargo for cargo in cargos if cargo not in membro.roles]
    if not cargos_para_adicionar:
        return True, "Aluno já tinha o material dos cursos aceitos."
    try:
        await membro.add_roles(
            *cargos_para_adicionar,
            reason="Solicitação de curso aceita — liberar material",
        )
    except discord.Forbidden:
        return False, "Sem permissão para conceder cargo de material do curso."
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao conceder material do curso: {erro}"
    return True, "Material do(s) curso(s) liberado."


async def remover_cargos_cursando(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str]:
    """Retira o cargo de material após a decisão final do pedido.

    O material serve para estudar enquanto o curso está em andamento.
    Depois da aprovação ou reprovação o comprovante (cargo_id) é o que
    importa; o acesso ao canal de estudo deixa de ser necessário.
    """
    cargos, erro = _cargos_cursando_das_chaves(membro.guild, chaves)
    if erro:
        return False, erro
    cargos_para_remover = [cargo for cargo in cargos if cargo in membro.roles]
    if not cargos_para_remover:
        return True, "Aluno não tinha cargo de material para remover."
    try:
        await membro.remove_roles(
            *cargos_para_remover,
            reason="Curso finalizado — retirar material de estudo",
        )
    except discord.Forbidden:
        return False, "Sem permissão para retirar cargo de material do curso."
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao retirar material do curso: {erro}"
    return True, "Material do(s) curso(s) retirado."


def montar_linhas_corpo_pedido(
    *,
    membro: discord.Member,
    registro: SolicitacaoCurso,
    saldo_restante: int | None = None,
) -> tuple[str, str]:
    """
    Retorna (titulo, corpo_markdown) para o card de agendamento.
    """

    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    forma = registro.forma_pagamento or "IN_GAME"
    observacao = registro.observacao_aluno

    valor_bruto = soma_valor_ingame(chaves)
    valor_a_pagar = int(registro.valor_ingame or 0)
    moedas = int(registro.moedas_debitadas or 0)
    cotacao = int(getattr(registro, "cotacao_moeda", 0) or 0)

    desconto_reais = moedas * cotacao if moedas and cotacao else 0

    # Se a cotação antiga não foi gravada, deduz pelo valor que falta no total.
    if moedas > 0 and desconto_reais == 0 and valor_bruto > valor_a_pagar:
        desconto_reais = valor_bruto - valor_a_pagar

    # ==========================================================
    # CURSO / PACOTE
    # ==========================================================

    if len(chaves) <= 1:
        chave = chaves[0] if chaves else registro.chave_curso
        dados = obter_curso(chave) or {}

        emoji = dados.get("emoji") or "📚"
        nome_curso = dados.get("nome", chave)
        valor_curso = int(dados.get("valor_ingame") or 0)

        titulo = f"{emoji} {nome_curso} — Pedido de Curso"

        corpo_cursos = (
            f"**🎓 Curso:** {menção_cargo_curso(chave)}\n"
            f"**💵 Valor catálogo:** `{formatar_reais(valor_curso)}`"
        )

    else:
        titulo = "📚 Pacote de Cursos — Pedido de Curso"

        linhas_itens = []

        for chave in chaves:
            dados = obter_curso(chave) or {}
            valor = formatar_reais(int(dados.get("valor_ingame") or 0))

            linhas_itens.append(f"> {menção_cargo_curso(chave)} — `{valor}`")

        corpo_cursos = (
            "### 🛒 Cursos Solicitados\n\n"
            + "\n".join(linhas_itens)
            + f"\n\n**💰 Subtotal catálogo:** `{formatar_reais(valor_bruto)}`"
        )

    # ==========================================================
    # INFORMAÇÕES DE PAGAMENTO
    # ==========================================================

    if forma == "MOEDAS":
        nota_instrutor = (
            "> 🪙 **Pagamento legado em MOEDAS** (já debitado do aluno).\n"
            "> Após **aprovar**, as moedas podem ser creditadas a você."
        )

    elif forma == "IN_GAME_COM_DESCONTO":
        nota_instrutor = (
            "> 💵 **Pagamento IN-GAME com desconto em moedas.**\n"
            "> Cobre o **valor a pagar** diretamente com o aluno no jogo.\n"
            "> Use **Registrar Pagamento** para gerar o comprovante do "
            "repasse ao hospital antes de aprovar."
        )

    elif forma == "IN_GAME":
        nota_instrutor = (
            "> 💵 **Pagamento IN-GAME (jogo).**\n"
            "> Confira o valor com o aluno **dentro do jogo antes de aplicar o curso**.\n"
            "> Use **Registrar Pagamento** para gerar o comprovante do "
            "repasse ao hospital antes de aprovar."
        )

    elif forma == "GRATUITO":
        nota_instrutor = "> 📋 **Pedido gratuito** (isenção).\n> Sem cobrança in-game."

    else:
        nota_instrutor = (
            "> 📋 Confira a forma de pagamento com o aluno antes de aplicar o curso."
        )

    # ==========================================================
    # OBSERVAÇÃO DO ALUNO
    # ==========================================================

    bloco_obs = ""

    if observacao:
        bloco_obs = f"\n### 📝 Observação do aluno\n\n> {observacao}\n"

    # ==========================================================
    # CORPO PRINCIPAL
    # ==========================================================

    corpo = (
        f"**👤 Aluno:** {membro.mention} (`{membro.id}`)\n"
        f"**📋 Pedido:** `#{registro.id}`\n"
        f"{bloco_obs}\n"
        f"### 💰 Pagamento\n\n"
        f"{corpo_cursos}\n"
        f"**💳 Forma de pagamento:** `{forma}`\n"
    )

    # ==========================================================
    # VALOR A PAGAR
    # ==========================================================

    if forma == "GRATUITO":
        corpo += "**💵 Valor a pagar in-game:** `R$ 0`\n"

    else:
        if moedas > 0:
            texto_cotacao = (
                formatar_reais(cotacao) if cotacao > 0 else "cotação do aluno"
            )

            corpo += f"**🪙 Desconto:** `{moedas}` moeda(s) ({texto_cotacao} cada"

            if desconto_reais > 0:
                corpo += f" · abate `{formatar_reais(desconto_reais)}`"

            corpo += ")\n"

        corpo += f"**💵 Valor a pagar in-game:** `{formatar_reais(valor_a_pagar)}`\n"

    # ==========================================================
    # REPASSE AO HOSPITAL
    # ==========================================================

    if forma not in ("GRATUITO", "MOEDAS"):
        total_repasse = repasse_total_do_pedido(registro)

        status_repasse = (
            "registrado"
            if getattr(registro, "repasse_registrado", False)
            else "pendente"
        )

        corpo += (
            f"**🏥 Repasse ao hospital:** "
            f"`{formatar_reais(total_repasse)}` · `{status_repasse}`\n"
        )

        # Detalhamento dos repasses ainda pendentes.
        if not getattr(registro, "repasse_registrado", False):
            feitos = ler_repasses_feitos(registro)
            grupos = montar_grupos_repasse(chaves, valor_a_pagar)

            if grupos:
                corpo += "\n**📋 Repasses por grupo:**\n"

                for grupo in grupos:
                    marca = "✓" if feitos.get(grupo["id"]) else "○"

                    corpo += (
                        f"> {marca} {grupo['rotulo']}: "
                        f"`{formatar_reais(int(grupo['repasse']))}`\n"
                    )

    # ==========================================================
    # ORIENTAÇÃO AO INSTRUTOR
    # ==========================================================

    corpo += f"\n{nota_instrutor}"

    return titulo, corpo


async def marcar_grupo_repasse(
    solicitacao_id: int,
    grupo_id: str,
) -> SolicitacaoCurso | None:
    """
    Marca um grupo de repasse como registrado.

    Quando todos os grupos do pedido estão ok, liga ``repasse_registrado``.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        feitos = ler_repasses_feitos(registro)
        feitos[grupo_id] = True
        registro.repasses_json = json.dumps(feitos, ensure_ascii=False)
        chaves = parse_chaves_json(
            registro.chaves_cursos_json,
            registro.chave_curso,
        )
        grupos = montar_grupos_repasse(chaves, int(registro.valor_ingame or 0))
        todos_ok = all(feitos.get(grupo["id"]) for grupo in grupos)
        if not grupos:
            todos_ok = True
        registro.repasse_registrado = todos_ok
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_repasse_registrado(solicitacao_id: int) -> SolicitacaoCurso | None:
    """Compatibilidade: marca o pedido inteiro como repasse concluído."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        registro.repasse_registrado = True
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro
