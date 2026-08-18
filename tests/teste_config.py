"""Protege IDs e chaves da configuração contra colisões silenciosas no Discord."""

import ast
from collections import defaultdict
from pathlib import Path

from src.config import CANAIS, CARGOS

CAMINHO_DA_CONFIGURACAO = Path(__file__).parents[1] / "src" / "config.py"

ALIASES_DE_CANAIS_COM_ID_COMPARTILHADO = {
    1486369093769760843: {
        "CANAL_ABRIR_SUPORTE_DUVIDAS",
        "CANAL_TICKETS_BAU",
        "GUIA_DUVIDAS_TICKET",
    },
    1486369193787134064: {
        "CANAL_PAINEL_SOLICITAR_CURSOS",
        "SOLICITAR_CURSO_RESGATE",
    },
    1486369195028517026: {
        "CANAL_PAINEL_SOLICITAR_PROMOCAO",
        "SOLICITAR_PROMOCAO_PARAMEDICO",
    },
    1535307402092478514: {
        "GUIA_TUTORIAIS",
        "PAINEL_TUTORIAIS",
    },
}


def obter_dicionario_literal_da_configuracao(nome_do_dicionario: str) -> ast.Dict:
    """Localiza o literal do dicionário para detectar chaves perdidas pelo Python."""
    arvore_da_configuracao = ast.parse(CAMINHO_DA_CONFIGURACAO.read_text())

    for no_da_arvore in arvore_da_configuracao.body:
        if not isinstance(no_da_arvore, ast.Assign):
            continue
        if not isinstance(no_da_arvore.value, ast.Dict):
            continue
        for alvo_da_atribuicao in no_da_arvore.targets:
            if not isinstance(alvo_da_atribuicao, ast.Name):
                continue
            if alvo_da_atribuicao.id == nome_do_dicionario:
                return no_da_arvore.value

    raise AssertionError(f"Não foi encontrado o dicionário {nome_do_dicionario}.")


def obter_chaves_repetidas_do_literal(nome_do_dicionario: str) -> set[str]:
    """Devolve chaves escritas mais de uma vez no mesmo literal da configuração."""
    dicionario_literal = obter_dicionario_literal_da_configuracao(nome_do_dicionario)
    chaves_encontradas: list[str] = []

    for chave_do_dicionario in dicionario_literal.keys:
        if isinstance(chave_do_dicionario, ast.Constant):
            chaves_encontradas.append(chave_do_dicionario.value)

    return {
        chave_do_dicionario
        for chave_do_dicionario in chaves_encontradas
        if chaves_encontradas.count(chave_do_dicionario) > 1
    }


def obter_valores_repetidos(dicionario_de_ids: dict[str, int]) -> dict[int, set[str]]:
    """Agrupa somente IDs que aparecem em mais de uma chave do dicionário."""
    chaves_por_identificador: defaultdict[int, set[str]] = defaultdict(set)

    for chave_do_dicionario, identificador_do_discord in dicionario_de_ids.items():
        chaves_por_identificador[identificador_do_discord].add(chave_do_dicionario)

    return {
        identificador_do_discord: chaves_do_identificador
        for (
            identificador_do_discord,
            chaves_do_identificador,
        ) in chaves_por_identificador.items()
        if len(chaves_do_identificador) > 1
    }


def teste_canais_e_cargos_nao_tem_chaves_duplicadas_no_codigo_fonte():
    chaves_duplicadas_em_canais = obter_chaves_repetidas_do_literal("CANAIS")
    chaves_duplicadas_em_cargos = obter_chaves_repetidas_do_literal("CARGOS")

    assert not chaves_duplicadas_em_canais, (
        "CANAIS não pode repetir chaves, pois Python descarta a definição anterior."
    )
    assert not chaves_duplicadas_em_cargos, (
        "CARGOS não pode repetir chaves, pois Python descarta a definição anterior."
    )


def teste_canais_so_tem_ids_repetidos_quando_sao_aliases_documentados():
    valores_repetidos = obter_valores_repetidos(CANAIS)

    assert valores_repetidos == ALIASES_DE_CANAIS_COM_ID_COMPARTILHADO, (
        "Um ID de canal repetido precisa ser um alias documentado, não uma colisão."
    )


def teste_cargos_nao_tem_ids_repetidos_que_apontariam_para_cargos_distintos():
    valores_repetidos = obter_valores_repetidos(CARGOS)

    assert not valores_repetidos, (
        "Cada nome de cargo deve apontar para um único cargo do Discord."
    )


def teste_todo_id_ativo_de_canal_e_cargo_tem_tamanho_valido_para_o_discord():
    dicionarios_de_ids = {
        "CANAIS": CANAIS,
        "CARGOS": CARGOS,
    }

    for nome_do_dicionario, dicionario_de_ids in dicionarios_de_ids.items():
        for chave_do_dicionario, identificador_do_discord in dicionario_de_ids.items():
            if identificador_do_discord == 0:
                assert chave_do_dicionario == "LOG_AUSENCIA", (
                    "Somente o log opcional de ausência pode usar zero desativado."
                )
                continue

            quantidade_de_digitos = len(str(identificador_do_discord))

            assert 17 <= quantidade_de_digitos <= 20, (
                f"{nome_do_dicionario}[{chave_do_dicionario!r}] precisa ter entre "
                "17 e 20 dígitos."
            )
