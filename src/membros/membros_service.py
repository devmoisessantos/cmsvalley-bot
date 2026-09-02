"""Consultas, formatação e mutações da ficha de membros (domínio membros).

Toda lógica de banco de gerenciar-membros fica aqui.
O painel só monta interface e chama estas funções.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import Any

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
    EventosGate,
    FaltaChamada,
    HistoricoCargo,
    HistoricoPromocao,
    Laudo,
    LogPlantao,
    MovimentacaoMoeda,
    Presenca,
    Punicao,
    Recrutamento,
    SnapshotCargosMembro,
    SolicitacaoAusencia,
    SolicitacaoCurso,
    SolicitacaoDemissao,
    SolicitacaoPromocao,
    Ticket,
    Usuario,
)

# Status canônicos da tabela usuarios (evita typo no modal livre)
STATUS_USUARIO_CANONICOS = (
    "VISITANTE",
    "ESTUDANTE",
    "PROVA",
    "APROVADO",
)


# ── Resolução de IDs ─────────────────────────────────────────────────────


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


# ── Leituras básicas ─────────────────────────────────────────────────────


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


async def listar_recrutamentos_candidato(
    discord_id: int, limite: int = 8
) -> list[Recrutamento]:
    """Histórico de processos de recrutamento do membro como candidato."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


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


async def listar_faltas_chamada(discord_id: int, limite: int = 8) -> list[FaltaChamada]:
    """Últimas faltas de chamada do membro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(FaltaChamada)
            .where(FaltaChamada.discord_id == discord_id)
            .order_by(FaltaChamada.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_chamadas_como_doutor(
    discord_id: int, limite: int = 5
) -> list[Chamada]:
    """Últimas chamadas em que o membro foi o doutor responsável."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Chamada)
            .where(Chamada.doutor_id == discord_id)
            .order_by(Chamada.criada_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


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
    """Consulta punições com filtro de atividade."""
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
    """Localiza casos pelo Discord ou FiveM."""
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
    """Busca advertências verbais de baú."""
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
    """Lista atendimentos recebidos."""
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
    """Lista atendimentos realizados pelo psicólogo."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Laudo)
            .where(Laudo.discord_id_psicologo == discord_id)
            .order_by(Laudo.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


# ── Novos blocos ─────────────────────────────────────────────────────────


async def listar_ausencias(
    discord_id: int, limite: int = 6
) -> list[SolicitacaoAusencia]:
    """Solicitações de ausência do membro (mais recentes)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoAusencia)
            .where(SolicitacaoAusencia.discord_id == discord_id)
            .order_by(SolicitacaoAusencia.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_demissoes(
    discord_id: int, limite: int = 5
) -> list[SolicitacaoDemissao]:
    """Pedidos de demissão do membro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoDemissao)
            .where(SolicitacaoDemissao.discord_id == discord_id)
            .order_by(SolicitacaoDemissao.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_solicitacoes_promocao(
    discord_id: int, limite: int = 6
) -> list[SolicitacaoPromocao]:
    """Pedidos de promoção."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao)
            .where(SolicitacaoPromocao.discord_id == discord_id)
            .order_by(SolicitacaoPromocao.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_historico_promocoes(
    discord_id: int, limite: int = 6
) -> list[HistoricoPromocao]:
    """Histórico permanente de promoções / rebaixamentos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(HistoricoPromocao)
            .where(HistoricoPromocao.discord_id == discord_id)
            .order_by(HistoricoPromocao.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_movimentacoes_moedas(
    discord_id: int, limite: int = 10
) -> list[MovimentacaoMoeda]:
    """Extrato recente de moedas."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.discord_id == discord_id)
            .order_by(MovimentacaoMoeda.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_presencas_gate(
    discord_id: int, limite: int = 8
) -> list[tuple[Presenca, EventosGate | None]]:
    """Últimas presenças GATE com dados do evento quando existir."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Presenca)
            .where(Presenca.discord_id == discord_id)
            .order_by(Presenca.confirmed_at.desc())
            .limit(limite)
        )
        presencas = list(resultado.scalars().all())
        saida: list[tuple[Presenca, EventosGate | None]] = []
        for presenca in presencas:
            evento = None
            if presenca.evento_id:
                r_ev = await sessao.execute(
                    select(EventosGate).where(EventosGate.id == presenca.evento_id)
                )
                evento = r_ev.scalar_one_or_none()
            saida.append((presenca, evento))
        return saida


async def listar_tickets_membro(discord_id: int, limite: int = 6) -> list[Ticket]:
    """Tickets abertos ou recentes do membro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Ticket)
            .where(Ticket.autor_discord_id == discord_id)
            .order_by(Ticket.aberto_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def listar_cursos_membro(
    discord_id: int, limite: int = 6
) -> list[SolicitacaoCurso]:
    """Solicitações de curso do membro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso)
            .where(SolicitacaoCurso.discord_id == discord_id)
            .order_by(SolicitacaoCurso.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def buscar_snapshot_cargos(
    discord_id: int,
) -> SnapshotCargosMembro | None:
    """Snapshot de cargos para rejoin."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SnapshotCargosMembro).where(
                SnapshotCargosMembro.discord_id == discord_id
            )
        )
        return resultado.scalar_one_or_none()


async def contagens_resumo_ficha(
    discord_id: int, id_fivem: str | None
) -> dict[str, int]:
    """Contagens rápidas para o resumo e badges do cabeçalho."""
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

    async with async_session() as sessao:
        aus_pend = await sessao.execute(
            select(func.count())
            .select_from(SolicitacaoAusencia)
            .where(
                SolicitacaoAusencia.discord_id == discord_id,
                SolicitacaoAusencia.status.in_(
                    ("pendente", "aprovada", "retorno_pendente")
                ),
            )
        )
        ausencias_abertas = int(aus_pend.scalar_one() or 0)

        dem_pend = await sessao.execute(
            select(func.count())
            .select_from(SolicitacaoDemissao)
            .where(
                SolicitacaoDemissao.discord_id == discord_id,
                SolicitacaoDemissao.status == "pendente",
            )
        )
        demissoes_pendentes = int(dem_pend.scalar_one() or 0)

        prom_pend = await sessao.execute(
            select(func.count())
            .select_from(SolicitacaoPromocao)
            .where(
                SolicitacaoPromocao.discord_id == discord_id,
                SolicitacaoPromocao.status == "PENDENTE",
            )
        )
        promocoes_pendentes = int(prom_pend.scalar_one() or 0)

        tickets_abertos = await sessao.execute(
            select(func.count())
            .select_from(Ticket)
            .where(
                Ticket.autor_discord_id == discord_id,
                Ticket.status.in_(("aberto", "assumido")),
            )
        )
        tickets_n = int(tickets_abertos.scalar_one() or 0)

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
        "ausencias_abertas": ausencias_abertas,
        "demissoes_pendentes": demissoes_pendentes,
        "promocoes_pendentes": promocoes_pendentes,
        "tickets_abertos": tickets_n,
    }


# ── Mutações administrativas ─────────────────────────────────────────────


async def zerar_ciclo_plantao(discord_id: int) -> bool:
    """Zera segundos_acumulados do ciclo atual. True se havia estado."""
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if not estado:
            return False
        estado.segundos_acumulados = 0
        estado.segmento_iniciado_em = (
            datetime.now(timezone.utc)
            if estado.toggle_ligado and estado.em_call_valida
            else None
        )
        await session.commit()
        return True


async def ajustar_saldo_moedas(
    discord_id: int,
    *,
    novo_saldo: int | None = None,
    delta: int | None = None,
    executor_id: int | None = None,
    referencia: str | None = None,
) -> tuple[int, int]:
    """
    Define saldo absoluto ou aplica delta.

    Registra movimentação AJUSTE_STAFF. Devolve (saldo_anterior, saldo_novo).
    """
    if novo_saldo is None and delta is None:
        raise ValueError("Informe novo_saldo ou delta.")
    if novo_saldo is not None and novo_saldo < 0:
        raise ValueError("Saldo não pode ser negativo.")

    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=discord_id, saldo_moedas=0)
            session.add(estado)
            await session.flush()

        antigo = int(estado.saldo_moedas or 0)
        if novo_saldo is not None:
            novo = int(novo_saldo)
        else:
            novo = antigo + int(delta or 0)
        if novo < 0:
            raise ValueError("Saldo resultante não pode ser negativo.")

        estado.saldo_moedas = novo
        session.add(
            MovimentacaoMoeda(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=novo - antigo,
                saldo_apos=novo,
                outro_discord_id=executor_id,
                referencia=(referencia or "ajuste admin ficha")[:200],
            )
        )
        await session.commit()
        return antigo, novo


async def editar_id_fivem_membro(
    discord_id: int,
    id_fivem: str | None,
) -> str | None:
    """
    Atualiza id_fivem em EstadoPlantao e Usuario.
    Passar None ou string vazia limpa o vínculo.
    Devolve o valor anterior.
    """
    valor = (id_fivem or "").strip() or None
    if valor is not None and not valor.isdigit():
        raise ValueError("ID FiveM deve conter apenas números.")

    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=discord_id)
            session.add(estado)
        antigo = estado.id_fivem
        estado.id_fivem = valor

        r2 = await session.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        usuario = r2.scalar_one_or_none()
        if usuario is None:
            usuario = Usuario(discord_id=discord_id, id_fivem=valor)
            session.add(usuario)
        else:
            usuario.id_fivem = valor
        await session.commit()
        return antigo


async def editar_status_usuario(
    discord_id: int,
    status: str,
    nickname: str | None = None,
    *,
    sincronizar_nick_discord: str | None = None,
) -> tuple[str | None, str]:
    """
    Atualiza status (e opcionalmente nick) na tabela usuarios.
    Devolve (status_anterior, status_novo).
    """
    status_limpo = status.strip().upper()
    if status_limpo not in STATUS_USUARIO_CANONICOS:
        raise ValueError(f"Status inválido. Use: {', '.join(STATUS_USUARIO_CANONICOS)}")

    async with async_session() as session:
        resultado = await session.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None:
            usuario = Usuario(discord_id=discord_id)
            session.add(usuario)
        antigo = usuario.status
        usuario.status = status_limpo
        if nickname is not None and nickname.strip():
            usuario.nickname_atual = nickname.strip()[:100]
        elif sincronizar_nick_discord:
            usuario.nickname_atual = sincronizar_nick_discord[:100]
        if status_limpo == "APROVADO":
            usuario.ja_foi_aprovado = True
        await session.commit()
        return antigo, status_limpo


# ── Formatação ───────────────────────────────────────────────────────────


def formatar_cargos_do_membro(membro: discord.Member) -> str:
    """Organiza menções de cargos para a ficha."""
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
    """Converte datas para o formato do Discord."""
    if data_e_hora is None:
        return "—"
    if data_e_hora.tzinfo is None:
        data_e_hora = data_e_hora.replace(tzinfo=timezone.utc)
    return f"<t:{int(data_e_hora.timestamp())}:d>"


def formatar_timestamp_relativo(data_e_hora: datetime | None) -> str:
    """Converte datas em prazo relativo do Discord."""
    if data_e_hora is None:
        return "—"
    if data_e_hora.tzinfo is None:
        data_e_hora = data_e_hora.replace(tzinfo=timezone.utc)
    return f"<t:{int(data_e_hora.timestamp())}:R>"


def membro_esta_no_servidor(alvo: Any) -> bool:
    """True se o alvo é um discord.Member real (não proxy de quem saiu)."""
    return isinstance(alvo, discord.Member)
