"""Consultas e formatação da ficha de membros (domínio membros).

Toda lógica de banco de gerenciar-membros fica aqui.
O painel só monta interface e chama estas funções.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from sqlalchemy import (
    func,
    or_,
    select,
)

from src.bau.bau_service import STATUS_ABERTOS_BAU
from src.database.conexao import async_session
from src.database.models import (
    AdvertenciaVerbalBau,
    CasoBau,
    Chamada,
    EstadoPlantao,
    FaltaChamada,
    HistoricoCargo,
    Laudo,
    LogPlantao,
    Punicao,
    Recrutamento,
    Usuario,
)


async def resolver_id_fivem_do_membro(discord_id: int) -> str | None:
    """Prioridade: EstadoPlantao → Usuario → último Recrutamento APROVADO."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor)

        resultado = await sessao.execute(
            select(Usuario.id_fivem).where(
                Usuario.discord_id == discord_id,
                Usuario.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor)

        resultado = await sessao.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        return str(valor) if valor else None


async def resolver_discord_id_por_fivem(id_fivem: str) -> int | None:
    """Resolve Discord ID a partir do passaporte FiveM."""
    id_texto = str(id_fivem).strip()
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.discord_id)
            .where(EstadoPlantao.id_fivem == id_texto)
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return int(valor)

        resultado = await sessao.execute(
            select(Usuario.discord_id).where(Usuario.id_fivem == id_texto).limit(1)
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return int(valor)

        resultado = await sessao.execute(
            select(Recrutamento.discord_id_candidato)
            .where(
                Recrutamento.id_fivem == id_texto,
                Recrutamento.discord_id_candidato.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        return int(valor) if valor else None


async def buscar_estado_plantao(discord_id: int) -> EstadoPlantao | None:
    """Obtém o estado de plantão sem criar um registro inexistente."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_usuario(discord_id: int) -> Usuario | None:
    """Obtém o cadastro persistido sem supor que todo membro já foi sincronizado."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_recrutamento_como_candidato(discord_id: int) -> Recrutamento | None:
    """Recupera o processo mais recente para exibir o contexto atual do candidato."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def estatisticas_como_recrutador(
    discord_id: int,
) -> tuple[int, int, list[Recrutamento]]:
    """Retorna (total APROVADO, total última semana, últimos 5 APROVADO)."""
    agora = datetime.now(timezone.utc)
    semana = agora - timedelta(days=7)
    async with async_session() as sessao:
        total = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
        )
        total_n = int(total.scalar_one() or 0)

        sem = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.data_fim.is_not(None),
                Recrutamento.data_fim >= semana,
            )
        )
        sem_n = int(sem.scalar_one() or 0)

        ultimos = await sessao.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(5)
        )
        lista = list(ultimos.scalars().all())
    return total_n, sem_n, lista


async def estatisticas_chamadas(discord_id: int) -> tuple[int, int]:
    """(faltas, chamadas_como_doutor)."""
    async with async_session() as sessao:
        faltas = await sessao.execute(
            select(func.count())
            .select_from(FaltaChamada)
            .where(FaltaChamada.discord_id == discord_id)
        )
        faltas_n = int(faltas.scalar_one() or 0)

        como_doutor = await sessao.execute(
            select(func.count())
            .select_from(Chamada)
            .where(Chamada.doutor_id == discord_id)
        )
        doutor_n = int(como_doutor.scalar_one() or 0)

    return faltas_n, doutor_n


async def tempo_total_segundos_plantao(discord_id: int) -> int:
    """Soma apenas durações concluídas para não contar plantões ainda abertos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.duracao_segundos.is_not(None),
            )
        )
        return int(resultado.scalar_one() or 0)


async def ultimos_logs_plantao(discord_id: int, limite: int = 8) -> list[LogPlantao]:
    """Traz os eventos mais recentes com limite para manter a ficha legível."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(LogPlantao)
            .where(LogPlantao.discord_id == discord_id)
            .order_by(LogPlantao.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_punicoes(
    discord_id: int,
    *,
    so_ativas: bool | None = None,
    limite: int = 10,
) -> list[Punicao]:
    """Consulta punições com filtro de atividade que distingue histórico e pendências.

    ``so_ativas`` aceita verdadeiro, falso ou nulo; o último caso não filtra o
    status. O limite evita carregar todo o histórico disciplinar em uma ficha.
    """
    async with async_session() as sessao:
        consulta = select(Punicao).where(Punicao.discord_id == discord_id)
        if so_ativas is True:
            consulta = consulta.where(Punicao.ativa.is_(True))
        elif so_ativas is False:
            consulta = consulta.where(Punicao.ativa.is_(False))
        consulta = consulta.order_by(Punicao.criada_em.desc()).limit(limite)
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def contar_punicoes_ativas(discord_id: int) -> int:
    """Produz o total para indicadores sem transferir registros completos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Punicao)
            .where(Punicao.discord_id == discord_id, Punicao.ativa.is_(True))
        )
        return int(resultado.scalar_one() or 0)


async def listar_historico_cargos(
    discord_id: int,
    limite: int = 12,
) -> list[HistoricoCargo]:
    """Retorna mudanças recentes de cargo na ordem adequada para auditoria."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(HistoricoCargo)
            .where(HistoricoCargo.discord_id == discord_id)
            .order_by(HistoricoCargo.data_hora.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_casos_bau_membro(
    *,
    discord_id: int | None,
    id_fivem: str | None,
    so_abertos: bool = True,
    limite: int = 8,
) -> list[CasoBau]:
    """Localiza casos pelo Discord ou FiveM para cobrir vínculos incompletos.

    Não consulta nada sem ao menos um identificador, prevenindo a exposição de
    casos de outros membros. Por padrão traz apenas casos ainda abertos.
    """
    async with async_session() as sessao:
        filtros = []
        if discord_id:
            filtros.append(CasoBau.discord_id == discord_id)
        if id_fivem:
            filtros.append(CasoBau.id_fivem == str(id_fivem))
        if not filtros:
            return []
        consulta = select(CasoBau).where(or_(*filtros))
        if so_abertos:
            consulta = consulta.where(CasoBau.status.in_(STATUS_ABERTOS_BAU))
        consulta = consulta.order_by(CasoBau.id.desc()).limit(limite)
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def listar_verbais_bau(
    *,
    discord_id: int | None,
    id_fivem: str | None,
    limite: int = 8,
) -> list[AdvertenciaVerbalBau]:
    """Busca advertências por qualquer identificador disponível do membro.

    Retorna uma lista vazia sem identificadores para impedir uma consulta ampla
    indevida. A ordenação prioriza as advertências registradas mais recentemente.
    """
    async with async_session() as sessao:
        filtros = []
        if discord_id:
            filtros.append(AdvertenciaVerbalBau.discord_id == discord_id)
        if id_fivem:
            filtros.append(AdvertenciaVerbalBau.id_fivem == str(id_fivem))
        if not filtros:
            return []
        resultado = await sessao.execute(
            select(AdvertenciaVerbalBau)
            .where(or_(*filtros))
            .order_by(AdvertenciaVerbalBau.criada_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_laudos_como_paciente(
    discord_id: int,
    limite: int = 8,
) -> list[Laudo]:
    """Lista atendimentos recebidos, limitando o histórico exibido na ficha."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Laudo)
            .where(Laudo.discord_id_paciente == discord_id)
            .order_by(Laudo.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_laudos_como_psicologo(
    discord_id: int,
    limite: int = 8,
) -> list[Laudo]:
    """Lista atendimentos realizados pelo psicólogo, dos mais recentes aos antigos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Laudo)
            .where(Laudo.discord_id_psicologo == discord_id)
            .order_by(Laudo.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def contagens_resumo_ficha(
    discord_id: int, id_fivem: str | None
) -> dict[str, int]:
    """Contagens rápidas para o resumo da ficha (badges nos blocos)."""
    punicoes_ativas = await contar_punicoes_ativas(discord_id)
    casos_bau = await listar_casos_bau_membro(
        discord_id=discord_id, id_fivem=id_fivem, so_abertos=True, limite=50
    )
    verbais = await listar_verbais_bau(
        discord_id=discord_id, id_fivem=id_fivem, limite=50
    )
    laudos_pac = await listar_laudos_como_paciente(discord_id, limite=50)
    laudos_psi = await listar_laudos_como_psicologo(discord_id, limite=50)
    hist_cargos = await listar_historico_cargos(discord_id, limite=50)
    faltas_n, doutor_n = await estatisticas_chamadas(discord_id)
    total_rec, _, _ = await estatisticas_como_recrutador(discord_id)

    return {
        "punicoes_ativas": punicoes_ativas,
        "casos_bau_abertos": len(casos_bau),
        "verbais_bau": len(verbais),
        "laudos_paciente": len(laudos_pac),
        "laudos_psicologo": len(laudos_psi),
        "historico_cargos": len(hist_cargos),
        "faltas_chamada": faltas_n,
        "chamadas_doutor": doutor_n,
        "recrutamentos": total_rec,
    }


def formatar_cargos_do_membro(membro: discord.Member) -> str:
    """Organiza menções em grupos para caberem com clareza na ficha do membro.

    Mantém a ordem de hierarquia e exclui ``@everyone``, que todos possuem e
    não acrescenta informação administrativa. Cada linha recebe até três cargos
    para evitar um bloco visual excessivamente largo no Discord.
    """
    cargos = [
        cargo
        for cargo in sorted(
            membro.roles,
            key=lambda cargo_para_ordenar: cargo_para_ordenar.position,
            reverse=True,
        )
        if cargo.name != "@everyone"
    ]
    if not cargos:
        return "_Nenhum cargo._"
    mencoes = [cargo.mention for cargo in cargos]
    linhas = []
    for indice in range(0, len(mencoes), 3):
        linhas.append(" · ".join(mencoes[indice : indice + 3]))
    return "\n".join(linhas)


def formatar_timestamp(data_e_hora: datetime | None) -> str:
    """Converte datas para o formato do Discord, assumindo UTC quando necessário."""
    if data_e_hora is None:
        return "—"
    if data_e_hora.tzinfo is None:
        data_e_hora = data_e_hora.replace(tzinfo=timezone.utc)
    return f"<t:{int(data_e_hora.timestamp())}:d>"


def formatar_timestamp_relativo(data_e_hora: datetime | None) -> str:
    """Converte datas em prazo relativo do Discord, assumindo UTC quando necessário."""
    if data_e_hora is None:
        return "—"
    if data_e_hora.tzinfo is None:
        data_e_hora = data_e_hora.replace(tzinfo=timezone.utc)
    return f"<t:{int(data_e_hora.timestamp())}:R>"
