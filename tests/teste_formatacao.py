"""Protege formatações do bot contra datas, valores e menções errados."""

from datetime import datetime, timezone

from src.config import CURSOS
from src.utils.formatacao import (
    agora_brasilia,
    formatar_data_completa,
    formatar_data_curta,
    formatar_data_hora,
    formatar_data_hora_local,
    formatar_data_hora_rodape,
    formatar_data_solicitacao,
    formatar_dinheiro,
    formatar_hms,
    formatar_intervalo_de_datas,
    formatar_mes_e_ano,
    formatar_reais,
    fuso_brasilia,
    mencionar_cargo,
    mencionar_cargo_do_curso,
    para_horario_brasilia,
)


def teste_fuso_brasilia_devolve_o_identificador_oficial_da_cidade_de_sao_paulo():
    fuso_horario = fuso_brasilia()

    assert fuso_horario.key == "America/Sao_Paulo", (
        "O bot deve usar o fuso oficial de Brasília em todos os textos humanos."
    )


def teste_para_horario_brasilia_devolve_none_quando_nao_recebe_data_hora():
    data_hora_convertida = para_horario_brasilia(None)

    assert data_hora_convertida is None, (
        "Uma data ausente deve continuar ausente, sem causar falha na tela."
    )


def teste_para_horario_brasilia_interpreta_data_sem_fuso_como_utc():
    data_hora_sem_fuso = datetime(2026, 8, 9, 2, 4, 29)

    data_hora_convertida = para_horario_brasilia(data_hora_sem_fuso)

    assert data_hora_convertida == datetime(
        2026,
        8,
        8,
        23,
        4,
        29,
        tzinfo=fuso_brasilia(),
    ), "Datas sem fuso devem ser tratadas como UTC e convertidas para Brasília."


def teste_para_horario_brasilia_converte_data_utc_com_fuso_para_brasilia():
    data_hora_utc = datetime(2026, 8, 9, 2, 4, 29, tzinfo=timezone.utc)

    data_hora_convertida = para_horario_brasilia(data_hora_utc)

    assert data_hora_convertida.hour == 23, (
        "A conversão de UTC para Brasília deve aplicar três horas de diferença."
    )
    assert data_hora_convertida.day == 8, (
        "A conversão precisa ajustar também a mudança de dia quando necessária."
    )


def teste_agora_brasilia_devolve_data_hora_com_fuso_de_brasilia():
    momento_atual = agora_brasilia()

    assert momento_atual.tzinfo == fuso_brasilia(), (
        "O relógio do bot deve trazer fuso explícito para impedir datas ambíguas."
    )


def teste_formatar_hms_transforma_3661_segundos_em_uma_hora_um_minuto_e_um_segundo():
    texto_formatado = formatar_hms(3661)

    assert texto_formatado == "01:01:01", (
        "3661 segundos devem aparecer como uma hora, um minuto e um segundo."
    )


def teste_formatar_hms_mantem_todos_os_campos_com_zero_quando_recebe_zero():
    texto_formatado = formatar_hms(0)

    assert texto_formatado == "00:00:00", (
        "Duração zerada deve preservar o formato de horas, minutos e segundos."
    )


def teste_formatar_hms_aplica_a_regra_do_divmod_para_duracao_negativa():
    texto_formatado = formatar_hms(-1)

    assert texto_formatado == "-1:59:59", (
        "A duração negativa deve expor o cálculo atual sem ocultar o sinal."
    )


def teste_formatar_dinheiro_preserva_valores_positivo_zero_e_negativo():
    valor_positivo = formatar_dinheiro(1_000_000)
    valor_zero = formatar_dinheiro(0)
    valor_negativo = formatar_dinheiro(-1_200)

    assert valor_positivo == "R$1.000.000", (
        "Valores positivos devem usar ponto de milhar."
    )
    assert valor_zero == "R$0", (
        "Valor zero deve continuar explícito no formato monetário."
    )
    assert valor_negativo == "R$-1.200", "Valores negativos devem preservar o sinal."


def teste_formatar_reais_formata_valor_positivo_zero_e_negativo_com_espaco_apos_sigla():
    valor_positivo = formatar_reais(1_000_000)
    valor_zero = formatar_reais(0)
    valor_negativo = formatar_reais(-1_200)

    assert valor_positivo == "R$ 1.000.000", (
        "Valores positivos devem usar ponto de milhar."
    )
    assert valor_zero == "R$ 0", (
        "Valor zero deve continuar explícito no formato monetário."
    )
    assert valor_negativo == "R$ -1.200", "Valores negativos devem preservar o sinal."


def teste_formatadores_de_data_usam_brasilia_e_devolvem_travessao_para_none():
    data_hora_utc = datetime(2026, 8, 9, 2, 4, 29, tzinfo=timezone.utc)

    assert formatar_data_hora(data_hora_utc) == "8 de Ago às 23:04"
    assert formatar_data_hora_local(data_hora_utc) == "2026-08-08 23:04:29"
    assert formatar_data_curta(data_hora_utc) == "08/08"
    assert formatar_data_completa(data_hora_utc) == "08/08/2026"
    assert formatar_mes_e_ano(data_hora_utc) == "Ago/2026"
    assert formatar_data_hora(None) == "—"
    assert formatar_data_hora_local(None) == "—"
    assert formatar_data_curta(None) == "—"
    assert formatar_data_completa(None) == "—"
    assert formatar_mes_e_ano(None) == "—"


def teste_formatadores_de_rodape_e_solicitacao_usam_a_data_fornecida_em_brasilia():
    data_hora_utc = datetime(2026, 8, 9, 2, 4, 29, tzinfo=timezone.utc)

    texto_do_rodape = formatar_data_hora_rodape(data_hora_utc)
    texto_da_solicitacao = formatar_data_solicitacao(data_hora_utc)

    assert texto_do_rodape == "8 ago de 2026 • 23:04"
    assert texto_da_solicitacao == "8 de Ago 2026 23:04"


def teste_formatar_intervalo_de_datas_mantem_separador_e_travessao_para_ausencias():
    inicio_utc = datetime(2026, 8, 9, 2, 4, tzinfo=timezone.utc)
    fim_utc = datetime(2026, 8, 10, 2, 4, tzinfo=timezone.utc)

    intervalo_padrao = formatar_intervalo_de_datas(inicio_utc, fim_utc)
    intervalo_personalizado = formatar_intervalo_de_datas(
        inicio_utc,
        fim_utc,
        separador=" / ",
    )
    intervalo_sem_datas = formatar_intervalo_de_datas(None, None)

    assert intervalo_padrao == "08/08 até 09/08"
    assert intervalo_personalizado == "08/08 / 09/08"
    assert intervalo_sem_datas == "— até —"


def teste_mencionar_cargo_e_cargo_de_curso_geram_mencao_ou_travessao_quando_ausente():
    chave_de_curso_existente = next(iter(CURSOS))
    id_do_cargo_do_curso = CURSOS[chave_de_curso_existente]["cargo_id"]

    assert mencionar_cargo(123456789012345678) == "<@&123456789012345678>"
    assert mencionar_cargo_do_curso(chave_de_curso_existente) == (
        f"<@&{id_do_cargo_do_curso}>"
    )
    assert mencionar_cargo_do_curso("curso_inexistente") == "—"
