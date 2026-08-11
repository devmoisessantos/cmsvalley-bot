# src/utils/deploy_logger.py
"""
Logs coloridos no console durante a subida do bot.

Basta importar e chamar as funções para acompanhar o deploy no terminal.
"""

import datetime
from typing import Any


class Cores:
    """Códigos ANSI para deixar o console mais legível."""

    VERDE = "\033[92m"
    VERMELHO = "\033[91m"
    AMARELO = "\033[93m"
    AZUL = "\033[94m"
    CIANO = "\033[96m"
    MAGENTA = "\033[95m"
    CINZA = "\033[90m"
    BRANCO = "\033[97m"
    NEGRITO = "\033[1m"
    RESET = "\033[0m"


def _horario_atual() -> str:
    """Retorna o horário atual no formato HH:MM:SS."""
    from zoneinfo import ZoneInfo

    return datetime.datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M:%S")


def _formatar(mensagem: str, cor: str, emoji: str = "") -> str:
    """Aplica horário, emoji e cor a uma mensagem de log."""
    horario = _horario_atual()
    return f"{Cores.CINZA}[{horario}]{Cores.RESET} {emoji} {cor}{mensagem}{Cores.RESET}"


def info(mensagem: str):
    """Log informativo (azul). Use para ações normais em andamento."""
    print(_formatar(mensagem, Cores.AZUL, "ℹ️"))


def sucesso(mensagem: str):
    """Log de sucesso (verde). Use quando algo terminar bem."""
    print(_formatar(mensagem, Cores.VERDE, "✅"))


def erro(mensagem: str):
    """Log de erro (vermelho). Use quando algo falhar."""
    print(_formatar(mensagem, Cores.VERMELHO, "❌"))


def aviso(mensagem: str):
    """Log de aviso (amarelo). Use para alertas não críticos."""
    print(_formatar(mensagem, Cores.AMARELO, "⚠️"))


def destaque(mensagem: str):
    """Log de destaque (magenta). Use para títulos ou marcos importantes."""
    print(_formatar(mensagem, Cores.MAGENTA + Cores.NEGRITO, "🔷"))


def etapa(numero: int, total: int, descricao: str):
    """
    Mostra o progresso de uma etapa.

    Exemplo: [1/5] Carregando cogs...
    """
    print(_formatar(f"[{numero}/{total}] {descricao}", Cores.CIANO, "📌"))


def separador(titulo: str = ""):
    """
    Imprime uma linha separadora com título opcional.

    Exemplo: ═══ INÍCIO DO DEPLOY ═══
    """
    if titulo:
        linha = f"═══ {titulo} ═══"
        print(f"\n{Cores.MAGENTA}{Cores.NEGRITO}{linha}{Cores.RESET}")
    else:
        print(f"{Cores.CINZA}{'─' * 60}{Cores.RESET}")


def inicio_deploy():
    """Marca o início do deploy com uma linha destacada."""
    separador("INÍCIO DO DEPLOY")


def fim_deploy():
    """Marca o fim do deploy com uma linha destacada."""
    separador("DEPLOY CONCLUÍDO")
    print()


def resumo_erro(comando: str, erro_ocorrido: Any):
    """
    Exibe um resumo formatado de erro.

    Use dentro de try/except para mostrar o que falhou.
    """
    print(_formatar(f"Erro em '{comando}'", Cores.VERMELHO, "💥"))
    tipo_do_erro = type(erro_ocorrido).__name__
    print(f"  {Cores.VERMELHO}{tipo_do_erro}: {erro_ocorrido}{Cores.RESET}")
