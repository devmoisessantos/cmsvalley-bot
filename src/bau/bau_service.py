"""Regras de negócio do monitoramento de baú."""

from __future__ import annotations

import asyncio
import json
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from zoneinfo import ZoneInfo

from sqlalchemy import (
    func,
    select,
)

from src.bau.bau_leitura_service import (
    LogBauParseado,
    parsear_mensagem_log_bau,
)
from src.config import (
    ALIASES_ITENS_BAU,
    HORAS_RESET_CICLO_BAU,
    LIMITES_BAU_CAMADA_1,
    LIMITES_BAU_CAMADA_2,
    PRAZO_DEVOLUCAO_BAU_MINUTOS,
    TIMEZONE_LOCAL,
    TOLERANCIA_EXTRA_BAU,
    VERBAIS_PARA_ADV1_BAU,
)
from src.database.conexao import async_session
from src.database.models import (
    AdvertenciaVerbalBau,
    CasoBau,
    ConfigBau,
    ContadorItemBau,
)
from src.utils.error_handling import ignorar_falha_cosmetica

# Status em que o caso ainda está aberto (botões ativos conforme regras)
STATUS_ABERTOS_BAU = ("AGUARDANDO", "GRAVE", "PRAZO_ESTOURADO")

# Lock por id_fivem para não corromper contador em logs simultâneos
_travas_por_id: dict[str, asyncio.Lock] = {}


def _trava_do_id(id_fivem: str) -> asyncio.Lock:
    if id_fivem not in _travas_por_id:
        _travas_por_id[id_fivem] = asyncio.Lock()
    return _travas_por_id[id_fivem]


async def obter_tolerancia_extra() -> int:
    """Tolerância além do limite diário (padrão config, override no banco)."""
    async with async_session() as sessao:
        registro = await sessao.get(ConfigBau, "tolerancia_extra")
        if registro is not None:
            try:
                return max(0, int(registro.valor))
            except ValueError as erro_em_obter_tolerancia_extra:
                # Enfeite que falhou: ler a tolerancia extra salva.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_obter_tolerancia_extra,
                    o_que_falhou="ler a tolerancia extra salva",
                )
    return int(TOLERANCIA_EXTRA_BAU)


async def obter_limites_camada_1() -> dict[str, int]:
    """Combina os limites diários padrão com substituições salvas no banco.

    Parte da configuração estática para que o monitor continue tendo valores
    válidos sem registros administrativos. Valores persistidos com a chave da
    primeira camada substituem apenas seus itens correspondentes.
    """
    limites = dict(LIMITES_BAU_CAMADA_1)
    async with async_session() as sessao:
        resultado = await sessao.execute(select(ConfigBau))
        for registro in resultado.scalars().all():
            if registro.chave.startswith("limite_1_"):
                item = registro.chave.removeprefix("limite_1_")
                try:
                    limites[item] = int(registro.valor)
                except ValueError:
                    continue
    return limites


async def obter_limites_camada_2() -> dict[str, int]:
    """Combina os limites graves padrão com substituições salvas no banco.

    Mantém a base configurada no projeto e aplica somente as chaves de segunda
    camada válidas, ignorando valores persistidos que não possam virar número.
    """
    limites = dict(LIMITES_BAU_CAMADA_2)
    async with async_session() as sessao:
        resultado = await sessao.execute(select(ConfigBau))
        for registro in resultado.scalars().all():
            if registro.chave.startswith("limite_2_"):
                item = registro.chave.removeprefix("limite_2_")
                try:
                    limites[item] = int(registro.valor)
                except ValueError:
                    continue
    return limites


async def salvar_config_bau(
    chave: str,
    valor: str,
    *,
    atualizado_por: int | None = None,
) -> None:
    """Cria ou atualiza uma opção administrativa do baú no banco.

    Além do valor textual, guarda quem realizou a mudança e o instante da
    atualização. Isso permite que os painéis sobrescrevam a configuração sem
    perder a rastreabilidade da decisão.
    """
    async with async_session() as sessao:
        registro = await sessao.get(ConfigBau, chave)
        if registro is None:
            registro = ConfigBau(chave=chave, valor=str(valor))
            sessao.add(registro)
        else:
            registro.valor = str(valor)
        registro.atualizado_por = atualizado_por
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


def quantidade_dispara_alerta(
    quantidade: int, limite_diario: int, tolerancia: int
) -> bool:
    """
    True só se passou de limite + tolerância (ex.: limite 1, tol 1 → alerta em 3+).
    """
    return quantidade > (limite_diario + tolerancia)


def chave_ciclo_atual(referencia: datetime | None = None) -> str:
    """Ex.: 2026-08-10_11 — ciclo vigente nas horas 0/11/17."""
    fuso = ZoneInfo(TIMEZONE_LOCAL)
    momento = (referencia or datetime.now(timezone.utc)).astimezone(fuso)
    horas = sorted(HORAS_RESET_CICLO_BAU)
    hora_ciclo = horas[0]
    for hora in horas:
        if momento.hour >= hora:
            hora_ciclo = hora
    return f"{momento.year:04d}-{momento.month:02d}-{momento.day:02d}_{hora_ciclo:02d}"


async def resolver_discord_id(id_fivem: str) -> int | None:
    """
    Resolve Discord ID a partir do passaporte FiveM (Usuario → Plantão → Recrutamento).
    """
    from src.database.models import (
        EstadoPlantao,
        Recrutamento,
        Usuario,
    )

    id_texto = str(id_fivem).strip()
    async with async_session() as sessao:
        resultado_usuario = await sessao.execute(
            select(Usuario.discord_id).where(Usuario.id_fivem == id_texto).limit(1)
        )
        discord_id = resultado_usuario.scalar_one_or_none()
        if discord_id:
            return int(discord_id)

        resultado_plantao = await sessao.execute(
            select(EstadoPlantao.discord_id)
            .where(EstadoPlantao.id_fivem == id_texto)
            .limit(1)
        )
        discord_id = resultado_plantao.scalar_one_or_none()
        if discord_id:
            return int(discord_id)

        resultado_rec = await sessao.execute(
            select(Recrutamento.discord_id_candidato)
            .where(
                Recrutamento.id_fivem == id_texto,
                Recrutamento.discord_id_candidato.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        discord_id = resultado_rec.scalar_one_or_none()
        if discord_id:
            return int(discord_id)
    return None


async def aplicar_movimento_item(
    *,
    id_fivem: str,
    nome_cidade: str,
    item_canonico: str,
    delta: int,
    ciclo: str,
) -> int:
    """
    Soma (ou subtrai) no contador do ciclo. Retorna quantidade líquida após update.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ContadorItemBau).where(
                ContadorItemBau.id_fivem == id_fivem,
                ContadorItemBau.item_canonico == item_canonico,
                ContadorItemBau.ciclo_chave == ciclo,
            )
        )
        contador = resultado.scalar_one_or_none()
        if contador is None:
            contador = ContadorItemBau(
                id_fivem=id_fivem,
                nome_cidade=nome_cidade,
                item_canonico=item_canonico,
                quantidade=0,
                ciclo_chave=ciclo,
            )
            sessao.add(contador)
            await sessao.flush()

        nova = contador.quantidade + delta
        if nova < 0:
            nova = 0
        contador.quantidade = nova
        contador.nome_cidade = nome_cidade or contador.nome_cidade
        contador.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        return nova


def ler_itens_do_caso(caso: CasoBau | None) -> dict[str, int]:
    """Lê a dívida agregada do caso (JSON → dict item→quantidade)."""
    if caso is None or not caso.itens_json:
        # Compatibilidade com casos antigos (um item só)
        if caso is not None and caso.item_canonico and caso.item_canonico != "agregado":
            return {caso.item_canonico: int(caso.quantidade_atual or 0)}
        return {}
    try:
        bruto = json.loads(caso.itens_json)
        if not isinstance(bruto, dict):
            return {}
        return {
            str(chave): int(valor) for chave, valor in bruto.items() if int(valor) > 0
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def serializar_itens(mapa_itens: dict[str, int]) -> str:
    """Converte apenas dívidas positivas em JSON estável para persistência."""
    limpo = {chave: int(valor) for chave, valor in mapa_itens.items() if int(valor) > 0}
    return json.dumps(limpo, ensure_ascii=False, sort_keys=True)


def formatar_bloco_itens_yaml(mapa_itens: dict[str, int]) -> str:
    """Bloco para o card: + ITEM: x30 roupas ..."""
    if not mapa_itens:
        return "```yaml\n(sem itens)\n```"
    linhas = [
        f"+ ITEM: x{quantidade} {item}"
        for item, quantidade in sorted(mapa_itens.items())
    ]
    return "```yaml\n" + "\n".join(linhas) + "\n```"


def caso_tem_item_grave(mapa_itens: dict[str, int], limites_2: dict[str, int]) -> bool:
    """Indica se alguma dívida já atingiu o limite que exige tratamento grave.

    Itens sem limite de segunda camada não são considerados, evitando elevar a
    gravidade de uma ocorrência só porque ela possui muitos tipos de item.
    """
    for item, quantidade in mapa_itens.items():
        limite_2 = limites_2.get(item)
        if limite_2 is not None and quantidade >= limite_2:
            return True
    return False


async def buscar_caso_aberto_por_passaporte(id_fivem: str) -> CasoBau | None:
    """Um único caso aberto por passaporte (agregado de todos os itens)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(CasoBau)
            .where(
                CasoBau.id_fivem == id_fivem,
                CasoBau.status.in_(STATUS_ABERTOS_BAU),
            )
            .order_by(CasoBau.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


# Alias legado — evita quebrar imports antigos
async def buscar_caso_aberto(
    id_fivem: str, item_canonico: str | None = None
) -> CasoBau | None:
    """Mantém compatibilidade e busca o caso aberto pelo passaporte.

    O parâmetro de item permanece aceito para não quebrar chamadas antigas,
    embora os casos atuais agreguem todas as dívidas de uma mesma pessoa.
    """
    return await buscar_caso_aberto_por_passaporte(id_fivem)


async def abrir_ou_atualizar_caso_agregado(
    *,
    id_fivem: str,
    nome_cidade: str,
    discord_id: int | None,
    mapa_itens: dict[str, int],
    e_grave: bool,
) -> tuple[CasoBau, bool]:
    """
    Um caso por passaporte. Atualiza a lista de itens (dívida).
    O prazo de 30 min só é definido na criação — não reinicia a cada retirada.
    """
    existente = await buscar_caso_aberto_por_passaporte(id_fivem)
    prazo = datetime.now(timezone.utc) + timedelta(minutes=PRAZO_DEVOLUCAO_BAU_MINUTOS)
    soma = sum(mapa_itens.values())
    itens_texto = serializar_itens(mapa_itens)

    async with async_session() as sessao:
        if existente is not None:
            caso = await sessao.get(CasoBau, existente.id)
            caso.itens_json = itens_texto
            caso.quantidade_atual = soma
            caso.item_canonico = "agregado"
            caso.nome_cidade = nome_cidade or caso.nome_cidade
            if discord_id:
                caso.discord_id = discord_id
            if e_grave:
                caso.e_grave = True
                # Não rebaixa PRAZO_ESTOURADO → mantém status se já estourou
                if caso.status != "PRAZO_ESTOURADO":
                    caso.status = "GRAVE"
            caso.atualizado_em = datetime.now(timezone.utc)
            await sessao.commit()
            await sessao.refresh(caso)
            return caso, False

        caso = CasoBau(
            id_fivem=id_fivem,
            nome_cidade=nome_cidade,
            discord_id=discord_id,
            item_canonico="agregado",
            quantidade_atual=soma,
            itens_json=itens_texto,
            status="GRAVE" if e_grave else "AGUARDANDO",
            e_grave=e_grave,
            expira_em=prazo,
            criado_em=datetime.now(timezone.utc),
            atualizado_em=datetime.now(timezone.utc),
        )
        sessao.add(caso)
        await sessao.commit()
        await sessao.refresh(caso)
        return caso, True


async def abrir_ou_atualizar_caso(
    *,
    id_fivem: str,
    nome_cidade: str,
    discord_id: int | None,
    item_canonico: str,
    quantidade: int,
    e_grave: bool,
) -> tuple[CasoBau, bool]:
    """Compatibilidade: redireciona para o fluxo agregado."""
    return await abrir_ou_atualizar_caso_agregado(
        id_fivem=id_fivem,
        nome_cidade=nome_cidade,
        discord_id=discord_id,
        mapa_itens={item_canonico: quantidade},
        e_grave=e_grave,
    )


async def marcar_dm_resultado(caso_id: int, *, falhou: bool) -> None:
    """Registra no banco se o aviso direto do caso alcançou o membro.

    Não falha quando o caso já não existe, pois o envio da mensagem pode correr
    em paralelo com sua resolução. A data também permite auditar a tentativa.
    """
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return
        caso.dm_falhou = falhou
        caso.dm_enviada_em = datetime.now(timezone.utc)
        caso.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def salvar_message_alerta(caso_id: int, message_id: int) -> None:
    """Guarda o identificador do alerta para que atualizações editem o mesmo card.

    Ignora casos removidos entre a publicação e a gravação, evitando que uma
    condição de corrida interrompa o processamento dos demais logs.
    """
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return
        caso.canal_alerta_message_id = message_id
        await sessao.commit()


async def resolver_caso(
    caso_id: int,
    *,
    por_discord_id: int | None,
    status: str = "RESOLVIDO",
    motivo_ignore: str | None = None,
) -> CasoBau | None:
    """Fecha ou ignora um caso e registra a pessoa responsável no banco.

    Permite informar um estado diferente de resolvido e um motivo de ignorar,
    truncado para caber no campo persistido. Retorna o caso atualizado ou
    `None` se ele já não existir.
    """
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return None
        caso.status = status
        caso.resolvido_por = por_discord_id
        caso.resolvido_em = datetime.now(timezone.utc)
        caso.atualizado_em = datetime.now(timezone.utc)
        if motivo_ignore:
            caso.motivo_ignore = motivo_ignore[:500]
        await sessao.commit()
        await sessao.refresh(caso)
        return caso


async def contar_verbais(id_fivem: str) -> int:
    """Conta as verbais anteriores do passaporte para decidir uma escalada.

    Consulta somente registros do tipo verbal, para que advertências escaladas
    não sejam contadas novamente e alterem a regra de reincidência.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(AdvertenciaVerbalBau)
            .where(
                AdvertenciaVerbalBau.id_fivem == id_fivem,
                AdvertenciaVerbalBau.tipo == "VERBAL",
            )
        )
        return int(resultado.scalar_one() or 0)


async def aplicar_verbal_automatica(caso: CasoBau) -> tuple[str, AdvertenciaVerbalBau]:
    """
    Aplica verbal ao estourar o prazo.
    Status vira PRAZO_ESTOURADO (não fecha o caso — libera ocorrência Valley).
    Na 3ª verbal, registra escalada ADV1.
    """
    qtd_verbais = await contar_verbais(caso.id_fivem)
    mapa_itens = ler_itens_do_caso(caso)
    resumo_itens = (
        ", ".join(
            f"{item} x{quantidade}" for item, quantidade in sorted(mapa_itens.items())
        )
        or caso.item_canonico
    )

    tipo = "VERBAL"
    motivo = f"Excesso de baú — prazo de devolução esgotado. Itens: {resumo_itens}."

    if qtd_verbais + 1 >= VERBAIS_PARA_ADV1_BAU:
        tipo = "ADV1_ESCALADA"
        motivo = (
            f"3ª advertência verbal de baú ({resumo_itens}). "
            "Escalada automática para ADV 1 — diretoria deve avaliar."
        )

    async with async_session() as sessao:
        registro = AdvertenciaVerbalBau(
            id_fivem=caso.id_fivem,
            discord_id=caso.discord_id,
            nome_cidade=caso.nome_cidade,
            caso_id=caso.id,
            item_canonico="agregado",
            motivo=motivo[:500],
            tipo=tipo,
            automatica=True,
            criada_em=datetime.now(timezone.utc),
        )
        sessao.add(registro)
        caso_db = await sessao.get(CasoBau, caso.id)
        if caso_db is not None:
            # Mantém aberto: libera botão Valley; diretoria decide o restante
            caso_db.status = "PRAZO_ESTOURADO"
            caso_db.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return tipo, registro


async def listar_casos_expirados() -> list[CasoBau]:
    """Casos ainda no prazo (AGUARDANDO/GRAVE) com expira_em vencido."""
    agora_utc = datetime.now(timezone.utc)
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(CasoBau).where(
                CasoBau.status.in_(("AGUARDANDO", "GRAVE")),
                CasoBau.expira_em.is_not(None),
                CasoBau.expira_em <= agora_utc,
            )
        )
        return list(resultado.scalars().all())


async def liberar_limite_manual(
    *,
    id_fivem: str,
    item_canonico: str,
    executor_id: int,
) -> str:
    """
    Zera contador do item no ciclo e remove esse item da dívida do caso.
    Se o caso ficar sem itens, encerra como IGNORADO.
    """
    ciclo = chave_ciclo_atual()
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ContadorItemBau).where(
                ContadorItemBau.id_fivem == id_fivem,
                ContadorItemBau.item_canonico == item_canonico,
                ContadorItemBau.ciclo_chave == ciclo,
            )
        )
        contador = resultado.scalar_one_or_none()
        if contador is not None:
            contador.quantidade = 0
            contador.atualizado_em = datetime.now(timezone.utc)

        casos = await sessao.execute(
            select(CasoBau).where(
                CasoBau.id_fivem == id_fivem,
                CasoBau.status.in_(STATUS_ABERTOS_BAU),
            )
        )
        for caso in casos.scalars().all():
            mapa = ler_itens_do_caso(caso)
            if item_canonico in mapa:
                del mapa[item_canonico]
            if not mapa:
                caso.status = "IGNORADO"
                caso.motivo_ignore = f"Liberação manual por <@{executor_id}>"
                caso.resolvido_por = executor_id
                caso.resolvido_em = datetime.now(timezone.utc)
                caso.itens_json = "{}"
                caso.quantidade_atual = 0
            else:
                caso.itens_json = serializar_itens(mapa)
                caso.quantidade_atual = sum(mapa.values())
                caso.atualizado_em = datetime.now(timezone.utc)

        await sessao.commit()
    return (
        f"Limite liberado para passaporte `{id_fivem}` / item `{item_canonico}` "
        f"no ciclo `{ciclo}`."
    )


async def processar_log_parseado(
    log: LogBauParseado,
) -> list[dict]:
    """
    Aplica movimentos e mantém **um caso agregado por passaporte**.
    - PEGOU acima do teto → inclui/atualiza item na dívida do caso
    - GUARDOU → reduz dívida; se zerar todos os itens, resolve o caso
    - Dívida (itens_json) sobrevive ao reset de ciclo
    """
    eventos: list[dict] = []
    if log.acao == "DESCONHECIDA":
        eventos.append({"tipo": "acao_desconhecida", "log": log})
        return eventos

    ciclo = chave_ciclo_atual()
    discord_id = await resolver_discord_id(log.id_fivem)
    delta_sinal = 1 if log.acao == "PEGOU" else -1

    async with _trava_do_id(log.id_fivem):
        limites_1 = await obter_limites_camada_1()
        limites_2 = await obter_limites_camada_2()
        tolerancia = await obter_tolerancia_extra()

        caso_aberto = await buscar_caso_aberto_por_passaporte(log.id_fivem)
        mapa_divida = ler_itens_do_caso(caso_aberto)

        for item in log.itens:
            if item.item_canonico is None:
                eventos.append(
                    {
                        "tipo": "item_desconhecido",
                        "nome": item.nome_bruto,
                        "id_fivem": log.id_fivem,
                    }
                )
                continue

            if item.item_canonico not in limites_1:
                continue

            delta = delta_sinal * item.quantidade
            quantidade_ciclo = await aplicar_movimento_item(
                id_fivem=log.id_fivem,
                nome_cidade=log.nome_cidade,
                item_canonico=item.item_canonico,
                delta=delta,
                ciclo=ciclo,
            )

            limite_1 = limites_1[item.item_canonico]
            teto_sem_alerta = limite_1 + tolerancia

            if delta < 0:
                # Devolução: reduz a dívida do caso (mesmo após reset de ciclo)
                if item.item_canonico in mapa_divida:
                    nova_divida = mapa_divida[item.item_canonico] - item.quantidade
                    if nova_divida <= teto_sem_alerta:
                        mapa_divida.pop(item.item_canonico, None)
                    else:
                        mapa_divida[item.item_canonico] = nova_divida
                continue

            # Retirada: se passou do teto, atualiza dívida com a qtd líquida do ciclo
            # (ou mantém a maior dívida já registrada no caso)
            if quantidade_dispara_alerta(quantidade_ciclo, limite_1, tolerancia):
                divida_anterior = mapa_divida.get(item.item_canonico, 0)
                mapa_divida[item.item_canonico] = max(divida_anterior, quantidade_ciclo)

        # Sem dívida restante → resolve caso aberto
        if not mapa_divida:
            if caso_aberto is not None:
                await resolver_caso(
                    caso_aberto.id,
                    por_discord_id=None,
                    status="RESOLVIDO",
                )
                eventos.append(
                    {
                        "tipo": "caso_resolvido_auto",
                        "caso_id": caso_aberto.id,
                        "item": "agregado",
                        "quantidade": 0,
                    }
                )
            return eventos

        e_grave = caso_tem_item_grave(mapa_divida, limites_2)
        caso, criado = await abrir_ou_atualizar_caso_agregado(
            id_fivem=log.id_fivem,
            nome_cidade=log.nome_cidade,
            discord_id=discord_id,
            mapa_itens=mapa_divida,
            e_grave=e_grave,
        )
        eventos.append(
            {
                "tipo": "caso_novo" if criado else "caso_atualizado",
                "caso": caso,
                "quantidade": caso.quantidade_atual,
                "limite_1": 0,
                "limite_2": None,
                "e_grave": e_grave,
                "discord_id": discord_id,
                "mapa_itens": mapa_divida,
            }
        )

    return eventos


def parsear_conteudo(conteudo: str) -> LogBauParseado | None:
    """Interpreta o texto do log usando os aliases configurados para o baú."""
    return parsear_mensagem_log_bau(conteudo, ALIASES_ITENS_BAU)
