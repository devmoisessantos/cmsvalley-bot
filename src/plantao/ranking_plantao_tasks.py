"""Tasks e comandos: ranking de chamadas + ranking de horas de plantão.

Atualização em tempo real (mesmo padrão do ranking de moedas):

1. ``loop_tempo_real_horas`` e ``loop_ranking_moedas`` rodam a cada 1 minuto.
2. Cada ciclo monta o card de novo e **edita** a mensagem persistente
   (ou cria se ainda não existir / se a mensagem sumiu).
3. O ID da mensagem fica em ``paineis_postados`` (nome do painel no config).
4. Nas horas, a contagem do período ``tempo_real`` soma logs fechados **e**
   o trecho ainda aberto em call — espelhando o saldo vivo das moedas.

Agendamentos oficiais:
- Sábado 11h00: fecha ciclo (apaga tempo real, finanças, posta semanal)
- Sábado 11h05: publica novo ranking tempo real (novo ciclo)
- Dia 1 às 11h: ranking mensal
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import (
    commands,
    tasks,
)
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
    NOME_PAINEL_RANKING_HORAS_TEMPO_REAL,
    RANKING_DIA_POST_MENSAL,
    RANKING_HORA_POST,
    RANKING_HORA_REINICIO_TEMPO_REAL_MINUTO,
    TIMEZONE_LOCAL,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.plantao.ranking_plantao_service import (
    gerar_view_ranking_chamadas,
    gerar_view_ranking_horas,
    historico_ja_publicado,
    montar_lista_premiados,
    salvar_historico_plantao,
)
from src.utils.formatacao import (
    formatar_hms,
    formatar_reais,
)
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_SUCESSO

logger = logging.getLogger(__name__)


class RankingPlantaoTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._post_semanal: set[str] = set()
        self._post_mensal: set[str] = set()
        self._reinicio_tempo_real: set[str] = set()
        self.loop_rankings.start()
        self.loop_tempo_real_horas.start()
        self.loop_ranking_moedas.start()
        logger.info(
            "🏆 RankingPlantaoTasks (chamadas + horas + tempo real + moedas) "
            "inicializado"
        )

    def cog_unload(self):
        """
        Interrompe os três loops para evitar publicações duplicadas após recarregar.
        """
        self.loop_rankings.cancel()
        self.loop_tempo_real_horas.cancel()
        self.loop_ranking_moedas.cancel()

    # ── Tempo real a cada 1 minuto (horas e moedas usam o mesmo ritmo) ────
    #
    # Como é aplicado (igual nos dois rankings):
    # 1. O loop dispara a cada 60 segundos depois do bot ficar ready.
    # 2. Monta a LayoutView com os dados atuais do banco (+ ao vivo nas horas).
    # 3. Busca o message_id salvo em paineis_postados e edita essa mensagem.
    # 4. Se a mensagem não existir mais, posta de novo e grava o novo id.
    # Assim o canal mostra sempre a contagem corrente, sem flood de mensagens.

    @tasks.loop(minutes=1)
    async def loop_tempo_real_horas(self):
        """
        Atualiza o card persistente de horas a cada minuto.

        Espelha o ``loop_ranking_moedas``: recalcula totais e edita a mesma
        mensagem no canal de ranking. Falhas ficam no log e não param o loop.
        """
        try:
            await self._atualizar_ou_criar_tempo_real_horas()
        except Exception as erro:
            logger.exception("Loop tempo real horas: %s", erro)

    @loop_tempo_real_horas.before_loop
    async def before_tempo_real(self):
        """Espera o bot conectar antes de publicar ou editar o ranking de horas."""
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking HORAS tempo real (1 min) ativo")
        try:
            from src.financas.financas_service import (
                reajustar_valores_trocas_pendentes,
            )

            guilda = self.bot.get_guild(int(GUILD_ID))
            if guilda is not None:
                await reajustar_valores_trocas_pendentes(guilda)
        except Exception as erro_reajuste:
            logger.exception(
                "Reajuste de trocas pendentes na subida: %s",
                erro_reajuste,
            )

    @tasks.loop(minutes=1)
    async def loop_ranking_moedas(self):
        """
        Mantém o ranking de moedas atualizado a cada minuto.

        Mesmo contrato do loop de horas: edita a mensagem persistente
        (saldo vivo em estado_plantao) e isola falhas do ciclo seguinte.
        """
        try:
            from src.plantao.carteira_ranking_service import atualizar_ranking_moedas

            await atualizar_ranking_moedas(self.bot)
        except Exception as erro:
            logger.exception("Loop ranking moedas: %s", erro)

    @loop_ranking_moedas.before_loop
    async def before_ranking_moedas(self):
        """Aguarda a conexão do bot antes de iniciar as atualizações de moedas."""
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking MOEDAS tempo real (1 min) ativo")

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction):
        """Reconhece botões de depósito após restart (custom_id dinâmico)."""
        if interacao.type is not discord.InteractionType.component:
            return
        data = interacao.data or {}
        custom_id = str(data.get("custom_id") or "")
        from src.plantao.carteira_panel import (
            CUSTOM_ID_DEP_APROVAR,
            CUSTOM_ID_DEP_RECUSAR,
            _processar_decisao_deposito,
        )

        if custom_id.startswith(CUSTOM_ID_DEP_APROVAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_DEP_APROVAR) :])
            except ValueError:
                return
            if pedido_id <= 0:
                return
            if interacao.response.is_done():
                return
            await _processar_decisao_deposito(interacao, pedido_id, aprovar=True)
        elif custom_id.startswith(CUSTOM_ID_DEP_RECUSAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_DEP_RECUSAR) :])
            except ValueError:
                return
            if pedido_id <= 0:
                return
            if interacao.response.is_done():
                return
            await _processar_decisao_deposito(interacao, pedido_id, aprovar=False)

    def _nome_painel_tempo_real(self, pagina: int) -> str:
        """Página 1 = nome base; 2+ = nome_2, nome_3…"""
        if pagina <= 1:
            return NOME_PAINEL_RANKING_HORAS_TEMPO_REAL
        return f"{NOME_PAINEL_RANKING_HORAS_TEMPO_REAL}_{pagina}"

    async def _listar_registros_tempo_real(self) -> list[PainelPostado]:
        """Todos os cards do ranking tempo real, ordenados por página."""
        async with async_session() as sessao:
            resultado = await sessao.execute(select(PainelPostado))
            todos = list(resultado.scalars().all())
        prefixo = NOME_PAINEL_RANKING_HORAS_TEMPO_REAL
        filtrados: list[PainelPostado] = []
        for registro in todos:
            nome = registro.nome_painel or ""
            if nome == prefixo or nome.startswith(prefixo + "_"):
                filtrados.append(registro)

        def _ordem(registro: PainelPostado) -> int:
            nome = registro.nome_painel or ""
            if nome == prefixo:
                return 1
            sufixo = nome[len(prefixo) + 1 :]
            try:
                return int(sufixo)
            except ValueError:
                return 999

        filtrados.sort(key=_ordem)
        return filtrados

    async def _buscar_registro_tempo_real(self) -> PainelPostado | None:
        lista = await self._listar_registros_tempo_real()
        return lista[0] if lista else None

    async def _salvar_registro_tempo_real_pagina(
        self, pagina: int, canal_id: int, message_id: int
    ) -> None:
        nome = self._nome_painel_tempo_real(pagina)
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == nome)
            )
            registro = resultado.scalar_one_or_none()
            if registro is None:
                sessao.add(
                    PainelPostado(
                        nome_painel=nome,
                        canal_id=canal_id,
                        message_id=message_id,
                    )
                )
            else:
                registro.canal_id = canal_id
                registro.message_id = message_id
            await sessao.commit()

    async def _apagar_registro_tempo_real(self) -> None:
        """Remove todos os cards (página 1, 2, 3…) do tempo real."""
        registros = await self._listar_registros_tempo_real()
        if not registros:
            return
        async with async_session() as sessao:
            for registro in registros:
                atual = await sessao.get(PainelPostado, registro.id)
                if atual is not None:
                    await sessao.delete(atual)
            await sessao.commit()

    async def _apagar_registros_tempo_real_a_partir_de(self, pagina: int) -> None:
        """Apaga páginas >= pagina (quando o ranking encolheu)."""
        registros = await self._listar_registros_tempo_real()
        for registro in registros:
            nome = registro.nome_painel or ""
            if nome == NOME_PAINEL_RANKING_HORAS_TEMPO_REAL:
                numero = 1
            else:
                try:
                    numero = int(nome.split("_")[-1])
                except ValueError:
                    continue
            if numero >= pagina:
                async with async_session() as sessao:
                    atual = await sessao.get(PainelPostado, registro.id)
                    if atual is not None:
                        await sessao.delete(atual)
                        await sessao.commit()

    async def _apagar_mensagens_tempo_real_no_canal(
        self,
        canal: discord.abc.Messageable,
    ) -> None:
        """
        Apaga no Discord todas as mensagens dos cards tempo real
        que ainda estão registradas no banco.
        """
        registros = await self._listar_registros_tempo_real()
        for registro in registros:
            try:
                mensagem = await canal.fetch_message(int(registro.message_id))
                await mensagem.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _republicar_todas_as_paginas_tempo_real(
        self,
        canal: discord.abc.Messageable,
        views: list,
    ) -> None:
        """
        Garante ordem dos cards: apaga tudo o que o bot controla e
        posta as páginas de novo, uma atrás da outra.
        """
        await self._apagar_mensagens_tempo_real_no_canal(canal)
        await self._apagar_registro_tempo_real()

        for indice, view in enumerate(views):
            pagina = indice + 1
            try:
                mensagem = await canal.send(view=view)
                await self._salvar_registro_tempo_real_pagina(
                    pagina,
                    canal.id,
                    mensagem.id,
                )
                logger.info(
                    "Ranking HORAS tempo real página %s/%s em #%s (republicado)",
                    pagina,
                    len(views),
                    getattr(canal, "name", canal.id),
                )
            except discord.HTTPException as erro_envio:
                logger.exception(
                    "Não publicou página %s do ranking horas: %s",
                    pagina,
                    erro_envio,
                )

    async def _atualizar_ou_criar_tempo_real_horas(self) -> None:
        """
        Publica ou edita o ranking de horas em tempo real.

        Com lista grande o ranking vira **várias mensagens** (cards de
        continuação). Cada página fica em ``paineis_postados``:
        ranking_horas_tempo_real, ranking_horas_tempo_real_2, …

        Se qualquer página sumiu, republica **todas** em sequência para
        não deixar cards fora de ordem no canal.
        """
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            return
        canal_id = CANAIS.get("RANKING_HORAS_PLANTAO") or 0
        canal = guild.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            return

        # Janela do fechamento semanal (sábado 11h00–11h04): não mexe
        # no tempo real. Evita corrida com o loop que fecha o ciclo.
        agora_local = datetime.now(ZoneInfo(TIMEZONE_LOCAL))
        if (
            agora_local.weekday() == 5
            and agora_local.hour == RANKING_HORA_POST
            and agora_local.minute < RANKING_HORA_REINICIO_TEMPO_REAL_MINUTO
        ):
            return

        views, contagem, inicio, fim, total = await gerar_view_ranking_horas(
            "tempo_real", guild=guild, modo_postagem=False
        )
        if not isinstance(views, list):
            views = [views]

        registros = await self._listar_registros_tempo_real()

        # Nenhuma página registrada: posta tudo pela primeira vez
        if not registros:
            await self._republicar_todas_as_paginas_tempo_real(canal, views)
            return

        # Tenta só editar. Se alguma página sumiu, para e republica tudo.
        precisa_republicar = False
        for indice, view in enumerate(views):
            if indice >= len(registros):
                # Ranking cresceu: páginas novas — só acrescenta no fim
                # se as anteriores ainda existirem (editadas acima).
                break
            registro = registros[indice]
            try:
                mensagem = await canal.fetch_message(int(registro.message_id))
                await mensagem.edit(view=view)
            except discord.NotFound:
                logger.warning(
                    "Card tempo real horas página %s sumiu — "
                    "republicando todas as páginas em ordem",
                    indice + 1,
                )
                precisa_republicar = True
                break
            except discord.HTTPException as erro_http:
                logger.warning(
                    "Falha ao editar página %s do ranking horas: %s",
                    indice + 1,
                    erro_http,
                )
                # Não republica por erro transitório (rate limit etc.)
                # para não floodar o canal. O próximo minuto tenta de novo.

        if precisa_republicar:
            await self._republicar_todas_as_paginas_tempo_real(canal, views)
            return

        # Páginas novas (ranking cresceu e as antigas ainda existem)
        if len(views) > len(registros):
            for indice in range(len(registros), len(views)):
                pagina = indice + 1
                try:
                    mensagem = await canal.send(view=views[indice])
                    await self._salvar_registro_tempo_real_pagina(
                        pagina,
                        canal.id,
                        mensagem.id,
                    )
                    logger.info(
                        "Ranking HORAS tempo real página %s/%s em #%s",
                        pagina,
                        len(views),
                        canal.name,
                    )
                except discord.HTTPException as erro_envio:
                    logger.exception(
                        "Não publicou página %s do ranking horas: %s",
                        pagina,
                        erro_envio,
                    )

        # Páginas a mais (ranking encolheu): apaga no Discord e no banco
        if len(registros) > len(views):
            for registro in registros[len(views) :]:
                try:
                    mensagem = await canal.fetch_message(int(registro.message_id))
                    await mensagem.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            await self._apagar_registros_tempo_real_a_partir_de(len(views) + 1)

    async def _fechar_ciclo_semanal_horas(self, referencia: datetime) -> None:
        """
        Sábado 11h:
        1) apaga card tempo real
        2) envia ganhadores + valores ao canal de finanças
        3) posta ranking semanal oficial
        """
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        canal_id = CANAIS.get("RANKING_HORAS_PLANTAO") or 0
        canal = guild.get_channel(int(canal_id)) if canal_id else None

        # Dados do ciclo que fecha (modo postagem = semana completa)
        views, contagem, inicio, fim, total = await gerar_view_ranking_horas(
            "semanal", guild=guild, referencia=referencia, modo_postagem=True
        )
        if not isinstance(views, list):
            views = [views]
        premiados = montar_lista_premiados(contagem)

        # Idempotência: se este ciclo semanal já foi fechado, não repete
        # ranking nem premiação (bot reiniciado no sábado 11h, etc.).
        ja_fechado = await historico_ja_publicado("horas_semanal", inicio, fim)
        if ja_fechado is not None:
            logger.info(
                "Ciclo semanal horas já fechado (histórico #%s) — "
                "só garante limpeza do card tempo real",
                ja_fechado.id,
            )
            if canal is not None:
                await self._apagar_mensagens_tempo_real_no_canal(canal)
            await self._apagar_registro_tempo_real()
            return

        # 1) Apaga todos os cards tempo real (página 1, 2, …)
        if canal is not None:
            await self._apagar_mensagens_tempo_real_no_canal(canal)
        await self._apagar_registro_tempo_real()

        # 2) Finanças
        await self._enviar_premiacao_financas(
            guild,
            premiados=premiados,
            inicio=inicio,
            fim=fim,
            total_segundos=total,
        )

        # 3) Ranking semanal oficial (só se ainda não existe para este período)
        if canal is not None:
            existente = await historico_ja_publicado("horas_semanal", inicio, fim)
            if existente is not None:
                logger.info(
                    "Ranking HORAS semanal já publicado (histórico #%s) — "
                    "não duplica",
                    existente.id,
                )
            else:
                try:
                    mensagem_publicada = None
                    for view in views:
                        mensagem_publicada = await canal.send(view=view)
                    await salvar_historico_plantao(
                        tipo="horas_semanal",
                        inicio=inicio,
                        fim=fim,
                        contagem=contagem,
                        total=total,
                        channel_id=canal.id,
                        message_id=(
                            mensagem_publicada.id if mensagem_publicada else None
                        ),
                    )
                    logger.info(
                        "Ranking HORAS semanal oficial postado em #%s",
                        canal.name,
                    )
                except discord.HTTPException as erro:
                    logger.exception(
                        "Falha ao postar ranking horas semanal: %s", erro
                    )

    async def _enviar_premiacao_financas(
        self,
        guild: discord.Guild,
        *,
        premiados: list[tuple[int, int, int, int]],
        inicio: datetime,
        fim: datetime,
        total_segundos: int,
    ) -> None:
        canal_id = CANAIS.get("CANAL_FINANCAS") or 0
        canal = guild.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            logger.warning("CANAL_FINANCAS ausente — premiação horas não postada")
            return

        from src.plantao.ranking_plantao_service import _formatar_data_curta

        if not premiados:
            linhas = (
                "_Nenhum participante com tempo registrado neste ciclo._\n"
                f"Período: **{_formatar_data_curta(inicio)}** até "
                f"**{_formatar_data_curta(fim)}**"
            )
        else:
            blocos = []
            soma_premios = 0
            for posicao, discord_id, segundos, premio in premiados:
                medalha = {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicao, "🏅")
                soma_premios += premio
                blocos.append(
                    f"{medalha} **#{posicao}** <@{discord_id}>\n"
                    f"↳ Tempo: **{formatar_hms(segundos)}** · "
                    f"Prêmio: **{formatar_reais(premio)}**"
                )
            linhas = (
                f"**Período:** {_formatar_data_curta(inicio)} até "
                f"{_formatar_data_curta(fim)}\n"
                f"**Tempo total da equipe:** {formatar_hms(total_segundos)}\n"
                f"**Total a repassar:** **{formatar_reais(soma_premios)}**\n\n"
                + "\n\n".join(blocos)
            )

        try:
            await canal.send(
                view=LogContainerView(
                    titulo="🏆 Premiação — Ranking de Horas (Plantão)",
                    linhas=linhas,
                    guild=guild,
                    cor=COR_SUCESSO,
                )
            )
            logger.info("Premiação horas enviada ao CANAL_FINANCAS")
        except discord.HTTPException as erro:
            logger.exception("Falha ao postar premiação horas em finanças: %s", erro)

    # ── Auto post semanal / mensal ────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def loop_rankings(self):
        """Fecha ciclos e publica rankings nos horários configurados.

        Usa chaves em memória para executar cada fechamento semanal, mensal ou reinício
        de tempo real uma só vez. Isso evita publicar dois rankings ou pagar prêmios
        repetidos quando o loop roda mais de uma vez dentro do mesmo minuto.
        """
        fuso_horario = ZoneInfo(TIMEZONE_LOCAL)
        agora = datetime.now(fuso_horario)

        # Sábado 11h00 — fecha horas + ranking semanal chamadas/horas
        if (
            agora.weekday() == 5
            and agora.hour == RANKING_HORA_POST
            and agora.minute == 0
        ):
            chave = f"semanal:{agora.strftime('%Y-%m-%d')}"
            if chave not in self._post_semanal:
                await self._fechar_ciclo_semanal_horas(agora)
                ok_c = await self._postar("chamada", "semanal", agora)
                # horas semanal já postado em _fechar_ciclo
                if ok_c:
                    pass
                self._post_semanal.add(chave)

        # Sábado 11h05 — novo card tempo real
        if (
            agora.weekday() == 5
            and agora.hour == RANKING_HORA_POST
            and agora.minute == RANKING_HORA_REINICIO_TEMPO_REAL_MINUTO
        ):
            chave_r = f"reinicio_tr:{agora.strftime('%Y-%m-%d')}"
            if chave_r not in self._reinicio_tempo_real:
                try:
                    canal_id = CANAIS.get("RANKING_HORAS_PLANTAO") or 0
                    guilda = self.bot.get_guild(int(GUILD_ID))
                    canal = (
                        guilda.get_channel(int(canal_id))
                        if guilda and canal_id
                        else None
                    )
                    if canal is not None:
                        await self._apagar_mensagens_tempo_real_no_canal(canal)
                    await self._apagar_registro_tempo_real()
                    await self._atualizar_ou_criar_tempo_real_horas()
                    self._reinicio_tempo_real.add(chave_r)
                    logger.info("Novo ciclo RANKING HORAS tempo real iniciado")
                except Exception as erro:
                    logger.exception("Reinício tempo real horas: %s", erro)

        # Dia 1 às 11h — mensal
        if (
            agora.day == RANKING_DIA_POST_MENSAL
            and agora.hour == RANKING_HORA_POST
            and agora.minute == 0
        ):
            chave = f"mensal:{agora.strftime('%Y-%m')}"
            if chave not in self._post_mensal:
                ok_c = await self._postar("chamada", "mensal", agora)
                ok_h = await self._postar("horas", "mensal", agora)
                if ok_c or ok_h:
                    self._post_mensal.add(chave)

    @loop_rankings.before_loop
    async def before_loop(self):
        """Espera a conexão do bot antes de acompanhar os horários de fechamento."""
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking chamadas/horas (sábado/mensal) ativo")

    async def _postar(
        self,
        categoria: str,
        periodo: str,
        referencia: datetime,
    ) -> bool:
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            return False

        if categoria == "chamada":
            canal_key = "RANKING_CHAMADAS"
            tipo_hist = f"chamada_{periodo}"
            gerador = gerar_view_ranking_chamadas
        else:
            canal_key = "RANKING_HORAS_PLANTAO"
            tipo_hist = f"horas_{periodo}"
            gerador = gerar_view_ranking_horas

        canal_id = CANAIS.get(canal_key) or 0
        if not canal_id:
            logger.warning("⚠️ CANAIS['%s'] não configurado", canal_key)
            return False
        canal = guild.get_channel(canal_id)
        if canal is None:
            logger.error("❌ Canal %s=%s não encontrado", canal_key, canal_id)
            return False

        try:
            resultado = await gerador(
                periodo, guild=guild, referencia=referencia, modo_postagem=True
            )
            views, contagem, inicio, fim, total = resultado
            if not isinstance(views, list):
                views = [views]
            existente = await historico_ja_publicado(tipo_hist, inicio, fim)
            if existente is not None:
                logger.info(
                    "Ranking %s já existe (histórico #%s) — não duplica",
                    tipo_hist,
                    existente.id,
                )
                return True
            mensagem = None
            for view in views:
                mensagem = await canal.send(view=view)
            await salvar_historico_plantao(
                tipo=tipo_hist,
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=canal.id,
                message_id=mensagem.id,
            )
            if categoria == "chamada":
                try:
                    from src.config import VALOR_UNITARIO_RANKING
                    from src.financas.financas_service import (
                        processar_fechamento_ranking,
                    )

                    await processar_fechamento_ranking(
                        self.bot,
                        guild,
                        chave_area="chamadas",
                        contagem=contagem,
                        inicio=inicio,
                        fim=fim,
                        total_unidades=total,
                        total_pago=total * VALOR_UNITARIO_RANKING,
                    )
                except Exception as erro_fin:
                    logger.exception("Fechamento financeiro chamadas: %s", erro_fin)

            logger.info("✅ Ranking %s postado em #%s", tipo_hist, canal.name)
            return True
        except Exception as erro:
            logger.exception("❌ Ranking %s/%s: %s", categoria, periodo, erro)
            return False


def _lista_historico(
    registros,
    guild: discord.Guild | None,
    *,
    titulo: str,
    horas: bool = False,
) -> discord.ui.LayoutView:
    from src.plantao.ranking_plantao_service import _formatar_data_curta

    if not registros:
        linhas = "_Nenhum ranking histórico encontrado._"
    else:
        blocos = []
        for registro in registros:
            periodo = (
                f"{_formatar_data_curta(registro.periodo_inicio)} → "
                f"{_formatar_data_curta(registro.periodo_fim)}"
            )
            if horas:
                metrica = f"⏱️ **{formatar_hms(registro.total_recrutamentos)}**"
            else:
                metrica = f"🩺 **{registro.total_recrutamentos}** chamadas"
            link = ""
            if registro.channel_id and registro.message_id and guild:
                link = (
                    f" • "
                    f"[abrir](https://discord.com/channels/{guild.id}/{registro.channel_id}/{registro.message_id})"
                )
            blocos.append(
                f"`#{registro.id}` **{registro.tipo}** `{periodo}`\n↳ {metrica}{link}"
            )
        linhas = "\n\n".join(blocos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Histórico • <t:{agora_ts}:f>"
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=discord.Color.dark_grey(),
        )
    )
    return view


async def setup(bot: commands.Bot):
    """Registra as tarefas e comandos responsáveis pelos rankings de plantão."""
    await bot.add_cog(RankingPlantaoTasks(bot))
