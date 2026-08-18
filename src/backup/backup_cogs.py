# src/backup/backup_cogs.py
"""
Comandos e listeners do sistema de backup.

Grupo único: /backup

  criar · listar · exportar · deletar · comparar · status
  restaurar-cargos · restaurar-canais · restaurar-membros · restaurar-tudo
  rejoin · sincronizar-membros · sincronizar-usuarios
  banco-painel · banco-exportar · banco-importar · banco-verificar · banco-listar

Respostas ao usuário passam por src.utils.mensagens.
Logs de canal passam por BackupLogger (Components V2).

Apenas Administradores.
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import (
    commands,
    tasks,
)

from src.backup.backup_gerenciador_service import BackupManager
from src.backup.backup_logger import BackupLogger
from src.backup.banco_no_discord_service import (
    PainelBancoBackupView,
    exportar_banco_para_canal,
    importar_snapshot_aditivo,
    ler_snapshot_do_anexo,
    listar_backups_do_canal,
    verificar_banco_vs_canal,
)
from src.backup.comparacao_service import DiffEngine
from src.backup.restauracao_service import RestoreManager
from src.backup.retrato_de_membros_service import (
    definir_rejoin,
    rejoin_esta_ativo,
    restaurar_cargos_no_rejoin,
    salvar_snapshot_membro,
    sincronizar_todos_os_membros,
)
from src.config import (
    AUTO_BACKUP_DB_INTERVAL_MINUTES,
    AUTO_BACKUP_INTERVAL_HOURS,
    BACKUP_DIR,
    CONFIRMATION_TIMEOUT,
    MAX_BACKUPS_PER_GUILD,
)
from src.membros.sincronizar_usuarios_service import (
    garantir_usuario_basico,
    sincronizar_usuarios_do_servidor,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
    excluir_mensagem,
    responder_view,
)
from src.utils.permissions import apenas_administrador
from src.utils.views import ViewDeConfirmacao

registrador = logging.getLogger(__name__)


class BackupCog(commands.Cog):
    """Todos os comandos de backup ficam neste único grupo."""

    grupo_backup = app_commands.Group(
        name="backup",
        description="Backup e restauração do servidor (somente Administradores)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.gerenciador = BackupManager()
        self.restaurador = RestoreManager()
        self.comparador = DiffEngine()
        self.logger = BackupLogger()
        self.tarefa_backup_automatico.change_interval(hours=AUTO_BACKUP_INTERVAL_HOURS)
        self.tarefa_backup_automatico.start()
        # Banco: a cada N minutos, só posta no LOG_BACKUP se o hash mudou
        minutos_banco = max(1, int(AUTO_BACKUP_DB_INTERVAL_MINUTES or 1))
        self.tarefa_backup_banco.change_interval(minutes=minutos_banco)
        self.tarefa_backup_banco.start()

    def cog_unload(self):
        """
        Cancela as tarefas periódicas para evitar execuções após descarregar o cog.
        """
        self.tarefa_backup_automatico.cancel()
        self.tarefa_backup_banco.cancel()

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    async def _carregar_backup_alvo(
        self,
        interacao: discord.Interaction,
        nome_arquivo: str | None,
    ) -> tuple[dict | None, str | None]:
        """Carrega o backup pedido ou o mais recente do servidor."""
        arquivo = nome_arquivo or self.gerenciador.nome_backup_mais_recente(
            interacao.guild.id
        )
        if not arquivo:
            return None, None

        backup = self.gerenciador.carregar_backup(interacao.guild.id, arquivo)
        return backup, arquivo

    async def _pedir_confirmacao(
        self,
        interacao: discord.Interaction,
        titulo: str,
        linhas: list[str],
    ) -> bool:
        """
        Mostra botões Confirmar/Cancelar e espera a escolha.

        A mensagem não some enquanto a pessoa não clicar.
        """
        corpo = "\n".join(f"`•` {linha}" for linha in linhas if linha is not None)

        # O titulo e o corpo vao DENTRO da view. Em Components V2 nao se manda
        # texto solto junto de uma LayoutView: o Discord recusa a mensagem.
        view_dos_botoes = ViewDeConfirmacao(
            autor_id=interacao.user.id,
            timeout=CONFIRMATION_TIMEOUT,
            titulo=titulo,
            pergunta=corpo,
        )

        mensagem = await responder_view(
            interacao,
            view_dos_botoes,
            ephemeral=True,
        )
        await view_dos_botoes.wait()
        asyncio.create_task(excluir_mensagem(mensagem, delay=3))

        pessoa_confirmou = view_dos_botoes.confirmado is True
        return pessoa_confirmou

    def _backup_de_seguranca(self, guilda: discord.Guild, autor: str) -> None:
        """Cria um backup automático antes de restaurar algo."""
        backup = self.gerenciador.criar_backup(
            guilda,
            criado_por=f"Auto (antes de restore por {autor})",
        )
        self.gerenciador.salvar_backup(backup)

    # ------------------------------------------------------------------
    # Backup automático
    # ------------------------------------------------------------------

    async def _backup_estrutural_se_mudou(self, guilda: discord.Guild) -> bool:
        """
        Compara o servidor com o último backup JSON.
        Só grava arquivo novo se houver qualquer diferença (ou se for o 1º backup).
        Retorna True se um backup novo foi salvo.
        """
        nome_recente = self.gerenciador.nome_backup_mais_recente(guilda.id)
        if nome_recente is None:
            backup = self.gerenciador.criar_backup(
                guilda,
                criado_por="Sistema (automático — primeiro backup)",
            )
            self.gerenciador.salvar_backup(backup)
            await self.logger.log(
                guilda,
                "🔄 Backup automático concluído",
                (
                    "Primeiro backup estrutural deste servidor.\n"
                    f"Cargos: {len(backup['roles'])} | "
                    f"Canais: {len(backup['channels'])} | "
                    f"Categorias: {len(backup['categories'])} | "
                    f"Membros: {len(backup['members'])}"
                ),
                COR_SUCESSO,
            )
            return True

        backup_anterior = self.gerenciador.carregar_backup(guilda.id, nome_recente)
        if backup_anterior is None:
            backup = self.gerenciador.criar_backup(
                guilda,
                criado_por="Sistema (automático)",
            )
            self.gerenciador.salvar_backup(backup)
            return True

        diferencas = self.comparador.comparar(guilda, backup_anterior)
        if not self.comparador.tem_diferenca(diferencas):
            # Silencioso: nada mudou desde o último snapshot
            registrador.warning(
                f"[backup] {guilda.name}: sem alterações estruturais — "
                "backup JSON ignorado."
            )
            return False

        resumo = self.comparador.resumir(diferencas)
        backup = self.gerenciador.criar_backup(
            guilda,
            criado_por="Sistema (automático — houve alteração)",
        )
        self.gerenciador.salvar_backup(backup)
        await self.logger.log(
            guilda,
            "🔄 Backup automático concluído",
            (
                "Alterações detectadas desde o último backup:\n"
                f"{resumo}\n\n"
                f"Cargos: {len(backup['roles'])} | "
                f"Canais: {len(backup['channels'])} | "
                f"Categorias: {len(backup['categories'])} | "
                f"Membros: {len(backup['members'])}"
            ),
            COR_SUCESSO,
        )
        return True

    @tasks.loop(hours=24)
    async def tarefa_backup_automatico(self):
        """
        Backup estrutural do Discord (cargos/canais/membros em JSON local).
        Só grava se houver diferença em relação ao último snapshot.
        """
        for guilda in self.bot.guilds:
            try:
                await self._backup_estrutural_se_mudou(guilda)
            except Exception as erro:
                registrador.error(f"Erro no backup estrutural de {guilda.name}: {erro}")

    @tarefa_backup_automatico.before_loop
    async def _antes_do_backup_automatico(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def tarefa_backup_banco(self):
        """
        A cada AUTO_BACKUP_DB_INTERVAL_MINUTES (padrão 1):
          - calcula o snapshot do Postgres
          - compara hash com o último JSON no LOG_BACKUP
          - se igual → silêncio total
          - se diferente → posta o novo arquivo no canal (sem spam de log extra)
        """
        for guilda in self.bot.guilds:
            try:
                resultado_banco = await exportar_banco_para_canal(
                    guilda,
                    autor="Sistema (verificação automática)",
                    forcar=False,
                )
                if resultado_banco.get("enviado"):
                    # Só um print no console — o próprio anexo no LOG_BACKUP já é o
                    # registro
                    registrador.info(
                        f"[backup-db] atualizado: {resultado_banco.get('arquivo')} "
                        f"hash={str(resultado_banco.get('hash') or '')[:12]} "
                        f"linhas={resultado_banco.get('linhas')}"
                    )
                # Sem alteração → nada no console (silencioso)
            except Exception as erro:
                registrador.error(f"[backup-db] erro em {guilda.name}: {erro}")

    @tarefa_backup_banco.before_loop
    async def _antes_do_backup_banco(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Listeners — snapshot vivo
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(
        self,
        antes: discord.Member,
        depois: discord.Member,
    ):
        """
        Atualiza o retrato de recuperação quando cargos ou apelido mudam.

        O evento recebe o membro antes e depois da alteração e grava o estado novo
        no banco. Isso preserva informações suficientes para devolver cargos em um
        rejoin, sem criar gravações desnecessárias para mudanças sem relevância.
        """
        cargos_antes = {cargo.id for cargo in antes.roles}
        cargos_depois = {cargo.id for cargo in depois.roles}
        cargos_mudaram = cargos_antes != cargos_depois
        nick_mudou = antes.nick != depois.nick

        if cargos_mudaram or nick_mudou:
            await salvar_snapshot_membro(depois)

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        """
        Conserva os dados de quem saiu e encerra seu recrutamento pendente.

        Grava no banco o último retrato do membro, necessário para recuperar seus
        cargos em uma volta ao servidor. Também tenta cancelar o recrutamento ativo
        para que uma saída não bloqueie um processo futuro; falhas nessa limpeza
        são registradas, mas não impedem a preservação do retrato.
        """
        # Cancela recrutamento ativo no banco (não trava reentrada / novo processo)
        try:
            from src.recrutamento.recrutamento_service import (
                cancelar_por_saida_do_servidor,
            )

            await cancelar_por_saida_do_servidor(membro.id)
        except Exception as erro_cancel:
            registrador.warning(
                f"⚠️ [rejoin] falha ao cancelar recrutamento de {membro.id}: "
                f"{erro_cancel}"
            )

        await salvar_snapshot_membro(membro)

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        """
        Prepara a conta que retornou e tenta devolver seus cargos salvos.

        Ignora bots, garante a linha básica do membro no banco e consulta o
        retrato preservado para restaurar cargos quando o rejoin estiver ativado.
        A restauração e seu resultado são registrados no canal de logs, evitando
        que uma mudança automática de permissões passe despercebida.
        """
        if membro.bot:
            return

        # Garante linha básica em usuarios (base das buscas do bot)
        try:
            await garantir_usuario_basico(membro)
        except Exception as erro:
            registrador.warning(f"Aviso ao criar usuario no join: {erro}")

        relatorio = await restaurar_cargos_no_rejoin(membro)
        if relatorio:
            await self.logger.log(
                membro.guild,
                "🔁 Rejoin — restore de cargos",
                "\n".join(relatorio),
                discord.Color.teal(),
                autor="Sistema",
            )

    # ------------------------------------------------------------------
    # /backup criar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="criar",
        description="Cria um backup manual do servidor agora",
    )
    @apenas_administrador()
    async def criar(self, interacao: discord.Interaction):
        """
        Produz um retrato manual do servidor e confirma o resultado ao administrador.

        Salva em disco cargos, canais, categorias e membros do servidor da
        interação. Depois atualiza os retratos individuais no banco e envia tanto
        uma resposta temporária quanto um registro no canal de backup, para que o
        arquivo criado possa ser localizado e auditado mais tarde.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)

        backup = self.gerenciador.criar_backup(
            interacao.guild,
            criado_por=str(interacao.user),
        )
        caminho = self.gerenciador.salvar_backup(backup)
        nome_arquivo = caminho.split("/")[-1]

        try:
            await sincronizar_todos_os_membros(interacao.guild)
        except Exception as erro:
            registrador.warning(f"Aviso ao sincronizar snapshots: {erro}")

        await enviar_card(
            interacao,
            titulo="✅ Backup criado",
            linhas=[
                f"Arquivo: `{nome_arquivo}`",
                f"Cargos: **{len(backup['roles'])}**",
                f"Canais: **{len(backup['channels'])}**",
                f"Categorias: **{len(backup['categories'])}**",
                f"Membros: **{len(backup['members'])}**",
            ],
            cor=COR_SUCESSO,
            delay=20,
        )
        await self.logger.log(
            interacao.guild,
            "💾 Backup manual criado",
            f"Arquivo `{nome_arquivo}` via /backup criar.",
            discord.Color.blue(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup listar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="listar",
        description="Lista os backups disponíveis neste servidor",
    )
    @apenas_administrador()
    async def listar(self, interacao: discord.Interaction):
        """
        Mostra até vinte retratos disponíveis para a guilda da interação.

        A resposta é enviada como card temporário no Discord. Quando não há
        arquivos, informa isso explicitamente para evitar que o administrador
        tente restaurar ou exportar um backup inexistente.
        """
        arquivos = self.gerenciador.listar_backups(interacao.guild.id)

        if not arquivos:
            await enviar_card(
                interacao,
                titulo="📂 Backups",
                linhas=["Nenhum backup encontrado para este servidor."],
                cor=COR_AVISO,
                delay=12,
            )
            return

        linhas = [
            f"`{indice + 1}.` {nome}" for indice, nome in enumerate(arquivos[:20])
        ]
        await enviar_card(
            interacao,
            titulo="📂 Backups disponíveis",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /backup exportar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="exportar",
        description="Baixa o backup mais recente (ou um específico) como arquivo",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def exportar(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Entrega ao administrador um arquivo de backup para download privado.

        Usa o nome indicado ou, se ele estiver vazio, escolhe o backup mais
        recente da guilda. Envia o JSON como anexo efêmero e agenda sua exclusão;
        assim o arquivo fica acessível por tempo suficiente sem permanecer exposto
        no histórico do Discord.
        """
        nome = arquivo or self.gerenciador.nome_backup_mais_recente(interacao.guild.id)
        if not nome:
            await enviar_card(
                interacao,
                titulo="📥 Exportar",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
                delay=10,
            )
            return

        caminho = self.gerenciador.caminho_completo(interacao.guild.id, nome)

        # Exceção legítima: precisa anexar arquivo (não cabe só em card).
        try:
            await interacao.response.send_message(
                content=f"📥 Backup: `{nome}`",
                file=discord.File(caminho),
                ephemeral=True,
            )
            mensagem = await interacao.original_response()
            asyncio.create_task(excluir_mensagem(mensagem, delay=60))
        except FileNotFoundError:
            await enviar_card(
                interacao,
                titulo="📥 Exportar",
                linhas=["Arquivo não encontrado no disco."],
                cor=COR_ERRO,
                delay=10,
            )

    # ------------------------------------------------------------------
    # /backup deletar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="deletar",
        description="Apaga um backup específico pelo nome do arquivo",
    )
    @app_commands.describe(arquivo="Nome exato do arquivo JSON")
    @apenas_administrador()
    async def deletar(self, interacao: discord.Interaction, arquivo: str):
        """
        Remove do disco somente o arquivo de backup escolhido pelo administrador.

        O nome recebido precisa identificar o arquivo da guilda atual. A resposta
        diferencia a remoção bem-sucedida de um nome ausente, evitando a impressão
        enganosa de que um retrato ainda recuperável foi apagado.
        """
        sucesso = self.gerenciador.deletar_backup(interacao.guild.id, arquivo)

        if sucesso:
            await enviar_card(
                interacao,
                titulo="🗑️ Backup deletado",
                linhas=[f"Arquivo `{arquivo}` removido com sucesso."],
                cor=COR_SUCESSO,
                delay=12,
            )
        else:
            await enviar_card(
                interacao,
                titulo="🗑️ Deletar backup",
                linhas=["Arquivo não encontrado."],
                cor=COR_ERRO,
                delay=10,
            )

    # ------------------------------------------------------------------
    # /backup comparar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="comparar",
        description="Compara um backup com o estado atual (não altera nada)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def comparar(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Exibe diferenças entre o servidor atual e um retrato sem alterar nada.

        Carrega o arquivo indicado, ou o mais recente, e transforma a comparação
        em linhas legíveis no card temporário. Essa prévia permite avaliar perdas
        ou mudanças antes de iniciar uma restauração que modificaria o Discord.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🔍 Comparar",
                linhas=["Nenhum backup encontrado para comparar."],
                cor=COR_AVISO,
            )
            return

        diff = self.comparador.comparar(interacao.guild, backup)
        resumo = self.comparador.resumir(diff)
        linhas = [linha for linha in resumo.split("\n") if linha.strip()]

        await enviar_card(
            interacao,
            titulo=f"🔍 Comparação · {nome}",
            linhas=linhas or ["Sem diferenças."],
            cor=COR_AVISO,
            delay=30,
        )

    # ------------------------------------------------------------------
    # /backup status
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="status",
        description="Mostra o status do sistema de backup",
    )
    @apenas_administrador()
    async def status(self, interacao: discord.Interaction):
        """
        Reúne indicadores que ajudam a conferir se a proteção está funcionando.

        Calcula quantos arquivos existem e quanto espaço ocupam no disco, identifica
        o último backup e informa a frequência automática, o limite de retenção, o
        canal de logs e o estado do rejoin. Envia esses dados somente à pessoa que
        executou o comando.
        """
        arquivos = self.gerenciador.listar_backups(interacao.guild.id)
        ultimo = arquivos[0] if arquivos else "Nenhum"

        pasta_do_servidor = os.path.join(BACKUP_DIR, str(interacao.guild.id))
        tamanho_total = 0
        if os.path.isdir(pasta_do_servidor):
            tamanho_total = sum(
                os.path.getsize(os.path.join(pasta_do_servidor, nome))
                for nome in os.listdir(pasta_do_servidor)
            )

        estado_rejoin = (
            "ligado ✅" if rejoin_esta_ativo(interacao.guild.id) else "desligado ⏸️"
        )

        await enviar_card(
            interacao,
            titulo="📊 Status do sistema de backup",
            linhas=[
                f"Backups salvos: **{len(arquivos)}**",
                f"Último backup: `{ultimo}`",
                f"Espaço usado: **{tamanho_total / 1024:.1f} KB**",
                f"Intervalo automático: a cada **{AUTO_BACKUP_INTERVAL_HOURS}h**",
                f"Máx. backups guardados: **{MAX_BACKUPS_PER_GUILD}**",
                f"Canal de logs: {self.logger.mencao_do_canal()}",
                f"Rejoin automático: **{estado_rejoin}**",
            ],
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /backup restaurar-cargos
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-cargos",
        description="Restaura apenas os cargos a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_cargos(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Recria cargos ausentes após mostrar uma prévia e pedir confirmação.

        O arquivo opcional seleciona o backup; sem ele, é usado o mais recente.
        Antes de modificar o Discord, simula as alterações, pede confirmação e
        grava um backup de segurança. Só então cria os cargos necessários, responde
        ao administrador e registra a intervenção no canal de logs.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar cargos",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa = await self.restaurador.restaurar_cargos(
            interacao.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · cargos (`{nome}`)",
            linhas=previa[:15] + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de cargos cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio = await self.restaurador.restaurar_cargos(
            interacao.guild, backup, dry_run=False
        )
        await enviar_card(
            interacao,
            titulo="✅ Cargos restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de cargos",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-canais
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-canais",
        description="Restaura categorias e canais a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_canais(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Recupera categorias e canais depois de uma prévia confirmada.

        A função combina as simulações de categorias e canais para revelar o efeito
        antes da mudança. Com a confirmação, salva um backup de segurança e altera
        a estrutura do Discord; por fim, envia o relatório ao administrador e ao
        canal de logs para deixar a restauração rastreável.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar canais",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa_categorias = await self.restaurador.restaurar_categorias(
            interacao.guild, backup, dry_run=True
        )
        previa_canais = await self.restaurador.restaurar_canais(
            interacao.guild, backup, dry_run=True
        )
        previa = (previa_categorias + previa_canais)[:15]

        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · canais (`{nome}`)",
            linhas=previa + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de canais cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio_categorias = await self.restaurador.restaurar_categorias(
            interacao.guild, backup, dry_run=False
        )
        relatorio_canais = await self.restaurador.restaurar_canais(
            interacao.guild, backup, dry_run=False
        )
        relatorio = relatorio_categorias + relatorio_canais

        await enviar_card(
            interacao,
            titulo="✅ Canais/categorias restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de canais",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-membros
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-membros",
        description="Restaura cargos/apelidos de membros que ainda estão no servidor",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_membros(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Repõe cargos e apelidos apenas de membros ainda presentes na guilda.

        O backup opcional define a origem do retrato e a função mostra uma simulação
        antes de pedir confirmação. Após criar um backup de segurança, modifica os
        perfis que ainda podem ser encontrados no Discord e avisa que pessoas que
        saíram não podem ser adicionadas novamente por esse processo.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar membros",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa = await self.restaurador.restaurar_membros(
            interacao.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · membros (`{nome}`)",
            linhas=[
                "Quem saiu do servidor não pode ser re-adicionado pelo bot.",
                *previa[:14],
                "",
                "Confirma aplicar?",
            ],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de membros cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio = await self.restaurador.restaurar_membros(
            interacao.guild, backup, dry_run=False
        )
        await enviar_card(
            interacao,
            titulo="✅ Membros restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de membros",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-tudo
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-tudo",
        description="Restaura cargos, categorias e canais (com backup de segurança)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_tudo(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        """
        Reconstitui a estrutura da guilda com uma confirmação reforçada.

        Carrega o backup indicado ou o mais recente e deixa claro que a operação
        inclui cargos, categorias e canais, mas não membros. Uma confirmação evita
        alterações acidentais; depois dela, grava um backup de segurança, modifica
        o Discord e registra o relatório completo no canal de logs.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar tudo",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo="⚠️ Restaurar estrutura completa",
            linhas=[
                f"Arquivo: `{nome}`",
                "Inclui cargos, categorias e canais.",
                "Um backup de segurança será criado antes.",
                "Membros NÃO entram aqui (use restaurar-membros).",
                "",
                "Tem certeza?",
            ],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração completa cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        resultado = await self.restaurador.restaurar_tudo(
            interacao.guild, backup, dry_run=False
        )
        relatorio = resultado["roles"] + resultado["categories"] + resultado["channels"]
        await enviar_card(
            interacao,
            titulo="✅ Restauração completa",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=30,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração completa",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup rejoin
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="rejoin",
        description="Liga ou desliga o restore automático de cargos ao voltar",
    )
    @app_commands.describe(ativo="True = ligado, False = desligado")
    @apenas_administrador()
    async def rejoin(self, interacao: discord.Interaction, ativo: bool):
        """
        Define se retornos ao servidor podem receber cargos do retrato salvo.

        O booleano recebido liga ou desliga essa proteção para a guilda atual. A
        escolha é persistida pela configuração de rejoin e divulgada em uma resposta
        temporária e no canal de logs, para que a equipe saiba quem a alterou.
        """
        definir_rejoin(interacao.guild.id, ativo)
        estado = "ligado ✅" if ativo else "desligado ⏸️"

        await enviar_card(
            interacao,
            titulo="⚙️ Rejoin automático",
            linhas=[f"Estado alterado para **{estado}** neste servidor."],
            cor=COR_INFO,
            delay=12,
        )
        await self.logger.log(
            interacao.guild,
            "⚙️ Rejoin automático",
            f"Estado alterado para **{estado}**.",
            COR_INFO,
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup sincronizar-membros
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="sincronizar-membros",
        description="Grava no banco o snapshot de cargos de todos os membros atuais",
    )
    @apenas_administrador()
    async def sincronizar_membros(self, interacao: discord.Interaction):
        """
        Atualiza no banco os retratos de todos os membros atualmente presentes.

        A operação grava cargos e dados necessários para a recuperação individual,
        portanto pode levar algum tempo e começa com uma resposta adiada. Ao final,
        informa a quantidade processada e o estado do rejoin, evitando confundir
        sincronização de dados com a ativação da restauração automática.
        """
        await interacao.response.defer(thinking=True, ephemeral=True)
        quantidade = await sincronizar_todos_os_membros(interacao.guild)
        estado_rejoin = (
            "ligado" if rejoin_esta_ativo(interacao.guild.id) else "desligado"
        )

        await enviar_card(
            interacao,
            titulo="✅ Snapshots sincronizados",
            linhas=[
                f"Membros salvos: **{quantidade}**",
                f"Rejoin automático: **{estado_rejoin}**",
            ],
            cor=COR_SUCESSO,
            delay=15,
        )

    # ------------------------------------------------------------------
    # /backup sincronizar-usuarios
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="sincronizar-usuarios",
        description="Alimenta a tabela usuarios com os membros atuais do servidor",
    )
    @apenas_administrador()
    async def sincronizar_usuarios(self, interacao: discord.Interaction):
        """Varre o Discord e preenche lacunas em usuarios (status, nick, id_fivem)."""
        await interacao.response.defer(thinking=True, ephemeral=True)

        resultado = await sincronizar_usuarios_do_servidor(interacao.guild)
        linhas = resultado.linhas_resumo()

        if resultado.erros:
            linhas.append(f"Avisos/erros: **{len(resultado.erros)}** (ver console)")
            for trecho in resultado.erros[:5]:
                registrador.info(f"[sincronizar-usuarios] {trecho}")

        await enviar_card(
            interacao,
            titulo="✅ Tabela usuarios sincronizada",
            linhas=linhas,
            cor=COR_SUCESSO,
            delay=40,
        )
        await self.logger.log(
            interacao.guild,
            "🗄️ Sincronização de usuarios",
            " | ".join(linhas),
            COR_SUCESSO,
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup banco-painel · banco-exportar · banco-importar · banco-verificar
    # Cofre do Postgres = mensagens + anexo JSON no LOG_BACKUP
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="banco-painel",
        description="Painel ephemeral: exportar, listar, verificar e importar o "
        "banco (JSON)",
    )
    @apenas_administrador()
    async def banco_painel(self, interacao: discord.Interaction):
        """
        Abre controles privados para administrar o cofre JSON do banco.

        Cria uma view vinculada ao identificador de quem executou o comando e a
        envia de modo efêmero. Essa vinculação impede que outro membro use botões
        capazes de exportar, conferir ou importar dados do banco.
        """
        view = PainelBancoBackupView(self.bot, interacao.user.id)
        await responder_view(interacao, view, ephemeral=True)

    @grupo_backup.command(
        name="banco-exportar",
        description="Exporta o Postgres em JSON e posta no canal LOG_BACKUP",
    )
    @app_commands.describe(
        forcar="Se verdadeiro, posta mesmo quando o hash for igual ao último do canal"
    )
    @apenas_administrador()
    async def banco_exportar(
        self,
        interacao: discord.Interaction,
        forcar: bool = False,
    ):
        """
        Publica no canal de backup um JSON do banco apenas quando necessário.

        Calcula o retrato do Postgres e compara seu hash com o último anexo do
        canal. O argumento booleano `forcar` permite publicar mesmo sem mudanças;
        sem ele, evita duplicatas. Quando envia, grava uma mensagem e um anexo no
        Discord e informa ao administrador o resultado.
        """
        await interacao.response.defer(ephemeral=True)
        resultado = await exportar_banco_para_canal(
            interacao.guild,
            autor=str(interacao.user),
            forcar=forcar,
        )
        if resultado.get("enviado"):
            await enviar_card(
                interacao,
                titulo="🗄️ Backup do banco enviado",
                linhas=[
                    f"Arquivo: `{resultado.get('arquivo')}`",
                    f"Tabelas: **{resultado.get('tabelas')}** · "
                    f"Linhas: **{resultado.get('linhas')}**",
                    f"Hash: `{str(resultado.get('hash') or '')[:20]}…`",
                    f"Canal: <#{resultado.get('canal_id')}>",
                ],
                cor=COR_SUCESSO,
                delay=20,
            )
            # Só o card + JSON no LOG_BACKUP — sem mensagem extra de log
        else:
            await enviar_card(
                interacao,
                titulo="🗄️ Nada postado",
                linhas=[
                    resultado.get("motivo") or "sem detalhes",
                    f"Hash: `{str(resultado.get('hash') or '')[:20]}…`",
                ],
                cor=COR_AVISO,
                delay=20,
            )

    @grupo_backup.command(
        name="banco-importar",
        description="Importa JSON do banco (só adiciona linhas que faltam — nunca "
        "apaga)",
    )
    @app_commands.describe(
        arquivo="Arquivo .json exportado (pode ter sido editado no VS Code)"
    )
    @apenas_administrador()
    async def banco_importar(
        self,
        interacao: discord.Interaction,
        arquivo: discord.Attachment,
    ):
        """
        Acrescenta ao banco as linhas válidas presentes em um anexo JSON.

        O anexo deve terminar em `.json`; depois de lido, seus registros ausentes
        são inseridos no banco sem apagar nem atualizar os que já existem. Esse
        comportamento aditivo reduz o risco de perder dados locais ao recuperar um
        arquivo editado ou exportado anteriormente.
        """
        await interacao.response.defer(ephemeral=True)
        nome = (arquivo.filename or "").lower()
        if not nome.endswith(".json"):
            await enviar_card(
                interacao,
                titulo="Arquivo inválido",
                linhas=["Envie um anexo com extensão `.json`."],
                cor=COR_ERRO,
                delay=15,
            )
            return
        try:
            snapshot = await ler_snapshot_do_anexo(arquivo)
            estatisticas = await importar_snapshot_aditivo(snapshot)
            await enviar_card(
                interacao,
                titulo="📥 Importação aditiva concluída",
                linhas=[
                    f"Arquivo: `{arquivo.filename}`",
                    f"Linhas inseridas: **{estatisticas.get('linhas_inseridas', 0)}**",
                    f"Já existiam: **{estatisticas.get('linhas_ja_existiam', 0)}**",
                    f"Tabelas tocadas: **{estatisticas.get('tabelas_tocadas', 0)}**",
                    f"Erros: **{estatisticas.get('erros', 0)}**",
                ],
                cor=COR_SUCESSO,
                delay=40,
            )
            await self.logger.log(
                interacao.guild,
                "📥 Importação de banco (JSON)",
                (
                    f"{arquivo.filename} · "
                    f"+{estatisticas.get('linhas_inseridas', 0)} inseridas · "
                    f"{estatisticas.get('linhas_ja_existiam', 0)} já existiam"
                ),
                COR_SUCESSO,
                autor=str(interacao.user),
            )
        except Exception as erro:
            await enviar_card(
                interacao,
                titulo="Falha ao importar",
                linhas=[str(erro)[:400]],
                cor=COR_ERRO,
                delay=25,
            )

    @grupo_backup.command(
        name="banco-verificar",
        description="Compara o banco local com o último JSON no LOG_BACKUP",
    )
    @apenas_administrador()
    async def banco_verificar(self, interacao: discord.Interaction):
        """
        Compara o estado do banco com o último cofre publicado no canal de backup.

        Gera e confronta hashes sem modificar dados, exibindo o motivo, as contagens
        e o link do anexo encontrado. Assim, a equipe pode decidir se deve exportar
        ou importar antes de executar uma sincronização desnecessária.
        """
        await interacao.response.defer(ephemeral=True)
        resultado = await verificar_banco_vs_canal(interacao.guild)
        if resultado.get("igual"):
            cor = COR_SUCESSO
            titulo = "✅ Banco = último backup do canal"
        else:
            cor = COR_AVISO
            titulo = "⚠️ Banco diferente do canal (ou sem backup)"
        await enviar_card(
            interacao,
            titulo=titulo,
            linhas=[
                resultado.get("motivo") or "",
                f"Hash local: `{str(resultado.get('hash_local') or '')[:24]}…`",
                f"Hash canal: `{str(resultado.get('hash_canal') or 'nenhum')[:24]}…`",
                f"Tabelas: **{resultado.get('tabelas')}** · "
                f"Linhas: **{resultado.get('linhas')}**",
                f"Link: {resultado.get('jump_url') or '—'}",
            ],
            cor=cor,
            delay=30,
        )

    @grupo_backup.command(
        name="banco-listar",
        description="Lista os últimos backups JSON postados no LOG_BACKUP",
    )
    @apenas_administrador()
    async def banco_listar(self, interacao: discord.Interaction):
        """
        Apresenta os últimos cofres JSON do banco com links de consulta e download.

        Busca até dez anexos no canal de backup e monta cards com o nome e o hash
        curto de cada um. Ao informar a ausência de anexos, evita que a equipe tente
        conferir ou restaurar um cofre que ainda não foi criado.
        """
        await interacao.response.defer(ephemeral=True)
        lista = await listar_backups_do_canal(interacao.guild, limite=10)
        if not lista:
            await enviar_card(
                interacao,
                titulo="Nenhum backup no canal",
                linhas=[
                    "Use `/backup banco-exportar` ou o painel para criar o primeiro."
                ],
                cor=COR_AVISO,
                delay=15,
            )
            return
        linhas = []
        for indice, item in enumerate(lista, start=1):
            hash_curto = (item.get("hash") or "?")[:12]
            linhas.append(
                f"**{indice}.** `{item.get('arquivo')}` · `{hash_curto}…`\n"
                f"[mensagem]({item.get('jump_url')}) · [download]({item.get('url')})"
            )
        await enviar_card(
            interacao,
            titulo="📋 Backups do banco no LOG_BACKUP",
            linhas=linhas,
            cor=COR_INFO,
            delay=60,
        )


async def setup(bot: commands.Bot):
    """Registra o cog de backup para disponibilizar seus comandos e tarefas."""
    await bot.add_cog(BackupCog(bot))
