"""Tasks e comandos: ranking de chamadas + ranking de horas de plantão.

Atualização em tempo real (mesmo padrão do ranking de moedas):

1. ``loop_tempo_real_horas`` e ``loop_ranking_moedas`` rodam a cada 1 minuto.
2. Cada ciclo monta o card de novo e edita as páginas cuja contagem
   mudou (assinatura por página). A **última página sempre é reeditada**:
   só ela traz o rodapé ``<t:unix:R>``, para o cliente mostrar
   "há 1 segundo", "há 37 segundos", etc.
3. O ID da mensagem fica em ``paineis_postados`` (nome do painel no config).
4. Nas horas, a contagem do período ``tempo_real`` soma logs fechados **e**
   o trecho ainda aberto em call — espelhando o saldo vivo das moedas.
5. Quando o plantão de alguém muda, a assinatura da página muda e só
   aquela página (ou as afetadas pela reordenação) é editada de novo.

Agendamentos oficiais (horas):
- Sábado 11h00: encerra contagem da semana (premiacao + histórico; sem re-postar)
- Sábado 11h01: inicia novo ciclo (card tempo real zerado)
- Dia 1 às 11h05: ranking mensal (total de horas do mês = soma das semanas)
"""

from __future__ import annotations

import asyncio
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
    RANKING_MINUTO_POST_MENSAL,
    TIMEZONE_LOCAL,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.plantao.ranking_plantao_service import (
    gerar_view_ranking_chamadas,
    gerar_view_ranking_horas,
    historico_ja_publicado,
    montar_lista_premiados,
    ordenar_ranking_individual,
    salvar_historico_plantao,
)
from src.utils.formatacao import (
    formatar_hms,
    formatar_reais,
)
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_SUCESSO
from src.utils.relogio import sincronizar_relogio_com_rede

logger = logging.getLogger(__name__)

# Mesmo padrão de montar_view_ranking_horas (entradas_por_card=25).
# Usado só para calcular a assinatura de cada página sem remontar o card.
ENTRADAS_POR_CARD_RANKING_HORAS = 25


class RankingPlantaoTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._post_semanal: set[str] = set()
        self._post_mensal: set[str] = set()
        self._reinicio_tempo_real: set[str] = set()
        # Assinatura da última contagem enviada por página (índice 0, 1, …).
        # Se a página não mudou, o loop não chama message.edit e evita 429.
        self._assinaturas_paginas_horas: dict[int, str] = {}
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

    @tasks.loop(seconds=60)
    async def loop_tempo_real_horas(self):
        """
        Ciclo de 1 minuto (rodapé <t:R> 1s → ~1 min → reinicia).

        - Páginas do meio: só edita se a contagem daquela página mudou.
        - Última página: sempre edita (leva o rodapé; zera o <t:R>).
        """
        try:
            await sincronizar_relogio_com_rede()
            await self._atualizar_ou_criar_tempo_real_horas()
        except Exception as erro:
            logger.exception("Loop tempo real horas: %s", erro)

    @loop_tempo_real_horas.before_loop
    async def before_tempo_real(self):
        """Espera o bot conectar antes de publicar ou editar o ranking de horas."""
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking HORAS tempo real (60 s) ativo")
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

    @tasks.loop(seconds=60)
    async def loop_ranking_moedas(self):
        """
        Ciclo de 1 minuto (rodapé <t:R> 1s → ~1 min → reinicia).

        Sempre reedita a mensagem única para zerar o rodapé. A view já
        traz saldos atuais; um edit/min não estoura rate limit.
        """
        try:
            from src.plantao.carteira_ranking_service import atualizar_ranking_moedas

            await sincronizar_relogio_com_rede()
            await atualizar_ranking_moedas(self.bot)
        except Exception as erro:
            logger.exception("Loop ranking moedas: %s", erro)

    @loop_ranking_moedas.before_loop
    async def before_ranking_moedas(self):
        """Aguarda a conexão do bot antes de iniciar as atualizações de moedas."""
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking MOEDAS tempo real (60 s) ativo")

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

    def _assinaturas_por_pagina_horas(
        self,
        contagem: dict[int, int],
    ) -> list[str]:
        """
        Uma assinatura por card do ranking, na mesma ordem das mensagens.

        A assinatura usa quem aparece naquela página e os segundos de cada
        um. Na última página entra também o total geral (o card mostra
        esse total). O rodapé relativo não entra na assinatura: a última
        página é reeditada todo ciclo de propósito, para renovar o
        ``<t:unix:R>`` mesmo quando a contagem não mudou.
        """
        ordenados = ordenar_ranking_individual(contagem)
        por_pagina = ENTRADAS_POR_CARD_RANKING_HORAS
        if not ordenados:
            return ["vazio"]

        total_geral = sum(segundos for _, segundos in ordenados)
        assinaturas: list[str] = []
        total_paginas = (len(ordenados) + por_pagina - 1) // por_pagina
        for indice_pagina in range(total_paginas):
            inicio = indice_pagina * por_pagina
            fim = inicio + por_pagina
            pedaco = ordenados[inicio:fim]
            partes = [f"{discord_id}:{segundos}" for discord_id, segundos in pedaco]
            assinatura = (
                f"{indice_pagina + 1}/{total_paginas}|" + "|".join(partes)
            )
            # Última página exibe o total da equipe — entra na assinatura
            if indice_pagina == total_paginas - 1:
                assinatura = f"{assinatura}|total:{total_geral}"
            assinaturas.append(assinatura)
        return assinaturas

    def _limpar_assinaturas_paginas_horas(self) -> None:
        """Zera o cache depois de republicar ou apagar os cards."""
        self._assinaturas_paginas_horas = {}

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
            self._limpar_assinaturas_paginas_horas()
            return
        async with async_session() as sessao:
            for registro in registros:
                atual = await sessao.get(PainelPostado, registro.id)
                if atual is not None:
                    await sessao.delete(atual)
            await sessao.commit()
        self._limpar_assinaturas_paginas_horas()

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

        # Janela do fechamento semanal (sábado 11h00): não mexe no tempo
        # real. O card só volta em 11h01 com o ciclo novo zerado.
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
        assinaturas_novas = self._assinaturas_por_pagina_horas(contagem)

        # Nenhuma página registrada: posta tudo pela primeira vez
        if not registros:
            await self._republicar_todas_as_paginas_tempo_real(canal, views)
            self._assinaturas_paginas_horas = {
                indice: assinatura
                for indice, assinatura in enumerate(assinaturas_novas)
            }
            return

        # Edita páginas cuja contagem mudou. A última página sempre é
        # reeditada: só ela carrega o rodapé ``<t:unix:R>``, e sem isso
        # o cliente mostra "há 2 minutos" mesmo com ranking correto.
        precisa_republicar = False
        ja_editou_alguma = False
        indice_ultima_pagina = len(views) - 1
        for indice, view in enumerate(views):
            if indice >= len(registros):
                # Ranking cresceu: páginas novas — só acrescenta no fim
                # se as anteriores ainda existirem (editadas acima).
                break

            assinatura_nova = (
                assinaturas_novas[indice]
                if indice < len(assinaturas_novas)
                else ""
            )
            assinatura_antiga = self._assinaturas_paginas_horas.get(indice)
            eh_ultima_pagina = indice == indice_ultima_pagina
            contagem_igual = (
                assinatura_antiga is not None
                and assinatura_antiga == assinatura_nova
            )
            if contagem_igual and not eh_ultima_pagina:
                continue

            # Primeiro ciclo após restart: assinaturas vazias. Aprende a
            # assinatura das páginas do meio SEM editar todas de uma vez
            # (evita 429). Só a última página edita (rodapé <t:R>).
            if assinatura_antiga is None and not eh_ultima_pagina:
                self._assinaturas_paginas_horas[indice] = assinatura_nova
                continue

            # Espaça edições entre páginas para não estourar o limite
            # do canal (duas PATCHs no mesmo segundo geram 429).
            if ja_editou_alguma:
                await asyncio.sleep(2.0)

            registro = registros[indice]
            try:
                mensagem = await canal.fetch_message(int(registro.message_id))
                await mensagem.edit(view=view)
                self._assinaturas_paginas_horas[indice] = assinatura_nova
                ja_editou_alguma = True
            except discord.NotFound:
                logger.warning(
                    "Card tempo real horas página %s sumiu — "
                    "republicando todas as páginas em ordem",
                    indice + 1,
                )
                precisa_republicar = True
                break
            except discord.HTTPException as erro_http:
                # Rate limit (429) e erros transitórios: só registra em
                # debug para não poluir o log a cada minuto.
                codigo = getattr(erro_http, "status", None)
                if codigo == 429:
                    logger.debug(
                        "Rate limit ao editar página %s do ranking horas; "
                        "tenta de novo no próximo ciclo.",
                        indice + 1,
                    )
                else:
                    logger.warning(
                        "Falha ao editar página %s do ranking horas: %s",
                        indice + 1,
                        erro_http,
                    )
                # Não republica por erro transitório (rate limit etc.)
                # para não floodar o canal. O próximo ciclo tenta de novo.
                # Não grava a assinatura: na próxima volta tenta de novo.

        if precisa_republicar:
            await self._republicar_todas_as_paginas_tempo_real(canal, views)
            self._assinaturas_paginas_horas = {
                indice: assinatura
                for indice, assinatura in enumerate(assinaturas_novas)
            }
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
                    if indice < len(assinaturas_novas):
                        self._assinaturas_paginas_horas[indice] = (
                            assinaturas_novas[indice]
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
            # Remove assinaturas das páginas que não existem mais
            indices_validos = set(range(len(views)))
            self._assinaturas_paginas_horas = {
                indice: assinatura
                for indice, assinatura in self._assinaturas_paginas_horas.items()
                if indice in indices_validos
            }

    async def _fechar_ciclo_semanal_horas(self, referencia: datetime) -> None:
        """
        Sábado 11h00 — encerra a contagem da semana:
        1) fecha segmentos abertos (tempo da virada fica na semana que termina)
        2) apaga card tempo real (volta zerado em 11h01)
        3) envia ganhadores + valores ao canal de finanças
        4) grava histórico (sem re-postar ranking no canal)
        """
        from src.recrutamento.ranking_service import obter_periodo_postagem_semanal
        from src.plantao.ranking_plantao_service import (
            buscar_horas_por_membro,
            montar_view_ranking_horas,
        )

        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        canal_id = CANAIS.get("RANKING_HORAS_PLANTAO") or 0
        canal = guild.get_channel(int(canal_id)) if canal_id else None

        inicio, fim_periodo = obter_periodo_postagem_semanal(referencia)

        # Idempotência: se este ciclo semanal já foi fechado, não repete
        # ranking nem premiação (bot reiniciado no sábado 11h, etc.).
        ja_fechado = await historico_ja_publicado(
            "horas_semanal", inicio, fim_periodo
        )
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

        # 1) Fecha segmentos abertos para o tempo na virada entrar na semana
        #    que está encerrando (e o ciclo novo começar limpo).
        try:
            from src.plantao.plantao_service import fechar_segmento_parcial_do_ciclo
            from src.plantao.carteira_service import cotacao_moeda_do_membro
            from src.database.models import EstadoPlantao

            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(EstadoPlantao).where(
                        EstadoPlantao.toggle_ligado.is_(True),
                        EstadoPlantao.em_call_valida.is_(True),
                        EstadoPlantao.segmento_iniciado_em.is_not(None),
                    )
                )
                estados_abertos = list(resultado.scalars().all())
            for estado in estados_abertos:
                membro = guild.get_member(int(estado.discord_id))
                cotacao = cotacao_moeda_do_membro(membro) if membro else None
                await fechar_segmento_parcial_do_ciclo(
                    int(estado.discord_id),
                    cotacao=cotacao,
                )
        except Exception as erro_fecha:
            logger.exception(
                "Falha ao fechar segmentos na virada do ciclo: %s",
                erro_fecha,
            )

        # Contagem final: inclui logs fechados até agora (virada),
        # usando o início da semana que termina.
        agora_utc = datetime.now(ZoneInfo("UTC"))
        contagem = await buscar_horas_por_membro(
            inicio,
            agora_utc,
            guild=guild,
            incluir_ao_vivo=False,
        )
        fim = fim_periodo
        _views, total = montar_view_ranking_horas(
            contagem, inicio, fim, periodo="semanal", guild=guild
        )
        premiados = montar_lista_premiados(contagem)

        # 2) Apaga todos os cards tempo real (página 1, 2, …)
        if canal is not None:
            await self._apagar_mensagens_tempo_real_no_canal(canal)
        await self._apagar_registro_tempo_real()

        # 3) Finanças
        await self._enviar_premiacao_financas(
            guild,
            premiados=premiados,
            inicio=inicio,
            fim=fim,
            total_segundos=total,
        )

        # 4) Histórico da semana (sem postar de novo no canal — o card
        #    tempo real já mostrou a contagem durante a semana).
        existente = await historico_ja_publicado("horas_semanal", inicio, fim)
        if existente is not None:
            logger.info(
                "Histórico HORAS semanal já existe (#%s) — não duplica",
                existente.id,
            )
        else:
            await salvar_historico_plantao(
                tipo="horas_semanal",
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=canal.id if canal is not None else None,
                message_id=None,
            )
            logger.info(
                "Ciclo semanal HORAS encerrado (histórico salvo, sem re-postar)"
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

        # Sábado 11h00 — encerra contagem horas + ranking semanal chamadas
        if (
            agora.weekday() == 5
            and agora.hour == RANKING_HORA_POST
            and agora.minute == 0
        ):
            chave = f"semanal:{agora.strftime('%Y-%m-%d')}"
            if chave not in self._post_semanal:
                await self._fechar_ciclo_semanal_horas(agora)
                await self._postar("chamada", "semanal", agora)
                # horas: só encerra (histórico + finanças), sem re-postar
                self._post_semanal.add(chave)

        # Sábado 11h01 — novo card tempo real (todos em 00:00:00)
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
                    logger.info(
                        "Novo ciclo RANKING HORAS tempo real iniciado (zerado)"
                    )
                except Exception as erro:
                    logger.exception("Reinício tempo real horas: %s", erro)

        # Dia 1 às 11h05 — ranking mensal (total de horas = soma das semanas)
        if (
            agora.day == RANKING_DIA_POST_MENSAL
            and agora.hour == RANKING_HORA_POST
            and agora.minute == RANKING_MINUTO_POST_MENSAL
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
