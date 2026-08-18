# Dívida técnica registrada — CMS Valley Bot

Este arquivo lista o que ainda **não** está de acordo com o `AGENTS.md`, com o
motivo de não ter sido resolvido e a ordem sugerida para resolver.

Ele existe por causa do Mandamento 10: *nunca quebrar o que já funciona*. Tudo
que está aqui exigiria mudar o miolo de código que hoje roda em produção sem
que haja como testar o resultado contra o Discord de verdade. Por isso a dívida
foi **mapeada e medida** em vez de ser resolvida às cegas.

O guardião (`ferramentas/guardiao.py`) conhece esta lista: as regras abaixo
aparecem no placar dele marcadas como *pendência conhecida* e não reprovam uma
mudança. Todas as outras regras do `AGENTS.md` estão em zero e **reprovam**.

---

## O que já está em zero

Estas regras foram levadas a zero e o guardião reprova qualquer regressão:

| Regra | O que ela impede |
|-------|------------------|
| `embed` | `discord.Embed` novo (o projeto usa Components V2) |
| `view-classico` | `discord.ui.View` novo (o projeto usa `LayoutView`) |
| `print` | `print()` solto no lugar de log |
| `except-silencioso` | `except` que engole o erro sem log |
| `except-pelado` | `except:` sem dizer qual erro |
| `resposta-direta` | responder ao usuário sem passar pelo `mensagens.py` |
| `walrus` | operador `:=` |
| `global` | variável global |
| `map-filter` | `map`, `filter`, `reduce` |
| `eval` | `eval`, `exec`, acesso dinâmico a atributo |
| `nome-curto` | nomes como `r`, `m`, `v`, `msg`, `ctx`, `e` |
| `nome-de-arquivo` | arquivo fora do padrão `{dominio}_{tipo}.py` |
| `id-magico` | id do Discord escrito no meio do código |
| `docstring` | função pública sem docstring |
| `docstring-de-arquivo` | módulo sem docstring de topo |

---

## Pendência 1 — Funções longas (167 casos)

O `AGENTS.md` pede no máximo 60 linhas por função. Hoje 167 funções
passam disso.

### Distribuição por tamanho

| Faixa | Quantidade |
|-------|-----------|
| acima de 200 linhas | 3 |
| de 121 a 200 linhas | 32 |
| de 91 a 120 linhas | 36 |
| de 61 a 90 linhas | 96 |

### Distribuição por domínio

| Domínio | Quantidade |
|---------|-----------|
| `plantao` | 32 |
| `backup` | 27 |
| `tickets` | 16 |
| `membros` | 10 |
| `recrutamento` | 9 |
| `punicoes` | 9 |
| `cursos` | 8 |
| `laudos` | 8 |
| `templates` | 7 |
| `ausencia` | 6 |
| `bau` | 6 |
| `promocoes` | 5 |
| `notificacoes` | 4 |
| `gate` | 4 |
| `demissao` | 3 |
| `financas` | 3 |
| `guia` | 3 |
| `utils` | 3 |
| `avaliacao` | 2 |
| `hierarquia` | 1 |
| `whitelist` | 1 |

### Por que não foi resolvido

Dividir uma função de 180 linhas que aplica punição, mexe em cargos e escreve no
banco significa mudar a ordem em que as coisas acontecem. Um erro aí não aparece
em teste automatizado: aparece com um membro perdendo cargo indevidamente. Sem
um servidor Discord de ensaio para conferir cada fluxo, dividir 167 funções de
uma vez seria trocar um problema de leitura por um risco de produção.

### Ordem sugerida para atacar

Comece pelas maiores, uma por vez, com um domínio por publicação:

| Ordem | Arquivo e linha | Função | Linhas |
|-------|-----------------|--------|--------|
| 1 | `src/tickets/tickets_service.py:753` | `montar_html_transcript` | 895 |
| 2 | `src/tickets/tickets_service.py:940` | `markdown_simples_para_html` | 231 |
| 3 | `src/templates/templates_cogs.py:672` | `_enviar_card_acao_bloco` | 225 |
| 4 | `src/tickets/tickets_views.py:1363` | `on_submit` | 198 |
| 5 | `src/promocoes/promocoes_views.py:362` | `_decidir` | 197 |
| 6 | `src/cursos/cursos_views.py:461` | `finalizar_pedido` | 197 |
| 7 | `src/recrutamento/recrutamento_service.py:251` | `validar_e_iniciar_recrutamento` | 189 |
| 8 | `src/tickets/tickets_service.py:1373` | `renderizar_bloco_conteudo` | 182 |
| 9 | `src/tickets/tickets_views.py:481` | `_executar_acao_membro` | 179 |
| 10 | `src/plantao/chamada/chamada_panel.py:1442` | `_ao_finalizar_chamada` | 179 |
| 11 | `src/plantao/plantao_listener.py:56` | `on_voice_state_update` | 166 |
| 12 | `src/punicoes/punicoes_service.py:196` | `executar_exoneracao` | 161 |
| 13 | `src/promocoes/promocoes_views.py:562` | `processar_escolha_trilha` | 159 |
| 14 | `src/tickets/tickets_views.py:115` | `__init__` | 158 |
| 15 | `src/templates/templates_cogs.py:105` | `_reconstruir` | 155 |
| 16 | `src/punicoes/punicoes_service.py:39` | `aplicar_punicao` | 154 |
| 17 | `src/templates/templates_modelo.py:466` | `_codigo_do_bloco` | 153 |
| 18 | `src/promocoes/promocoes_service.py:179` | `montar_checklist_trilha` | 153 |
| 19 | `src/plantao/chamada/chamada_panel.py:415` | `_processar_print_ems` | 153 |
| 20 | `src/demissao/demissao_panel.py:426` | `processar_decisao_demissao` | 148 |
| 21 | `src/plantao/chamada/chamada_panel.py:1828` | `_enviar_log_chamada_canal` | 142 |
| 22 | `src/ausencia/ausencia_panel.py:1062` | `processar_decisao_ausencia` | 140 |
| 23 | `src/ausencia/ausencia_panel.py:1205` | `processar_decisao_retorno` | 136 |
| 24 | `src/tickets/tickets_views.py:894` | `processar_clique_botao_ticket` | 135 |
| 25 | `src/recrutamento/aprovacao_panel.py:387` | `escolher` | 133 |

### Caso especial: o transcript dos tickets

`montar_html_transcript` (`src/tickets/tickets_service.py:753`) tem 895 linhas e
é sozinha o maior problema do repositório — quase quatro vezes a segunda
colocada. Ela é grande por um motivo específico: são cerca de quinze funções
aninhadas que montam o HTML do transcript, todas usando por closure as mesmas
três variáveis (`ticket`, `mensagens`, `guilda`).

O caminho correto é criar `src/tickets/tickets_transcript_service.py`, subir
essas funções aninhadas para o nível do módulo passando o que elas precisam por
parâmetro, e extrair o CSS e o molde da página para constantes com nome. O
`tickets_service.py` então só importa `montar_html_transcript` do novo módulo,
mantendo o nome público igual para não mexer em quem chama.

Isso não foi feito aqui porque exige conferir que o HTML gerado saiu idêntico ao
de antes, e o único jeito honesto de conferir isso é gerar um transcript real de
um ticket real antes e depois.

---

## Pendência 2 — Linhas acima de 88 colunas (48 casos)

Este número saiu de 234 para 48. O que sobrou é o resíduo que
não dá para cortar sem estragar o conteúdo.

| Tipo | Quantidade | Por que ficou |
|------|-----------|---------------|
| Texto sem espaço onde cortar | 24 | Palavra ou trecho contínuo mais longo que o limite |
| Uma única interpolação longa | 16 | A linha é uma só expressão `{...}`, não há texto onde cortar |
| Link que não pode ser cortado | 7 | Cortar uma URL no meio a transforma numa URL errada |
| Código puro | 1 | Nome de parâmetro ou chamada que já está no menor formato possível |

O `ruff format` também não consegue encurtar essas linhas: quando o script deste
projeto quebrava algumas delas à força, o próprio formatador as juntava de volta
na passada seguinte, porque o resultado quebrado não era mais legível que o
original.

---

## Pendência 3 — `_logger.py` em todos os domínios

O `AGENTS.md` descreve `{dominio}_logger.py` como parte do padrão de arquivos.
Hoje apenas oito domínios têm esse arquivo, e por uma razão: só oito domínios
publicam log visual em canal do Discord.

Criar dezessete arquivos `_logger.py` vazios só para o padrão bater seria
cerimônia sem função — o oposto do que o `AGENTS.md` pede. A decisão registrada
foi: **`_logger.py` é exigido apenas nos domínios que publicam log visual.**
Isso está escrito no próprio `AGENTS.md`.

O que continua como dívida de verdade: nos domínios que hoje montam o card de
log direto no meio do serviço (cursos, demissao, membros, notificacoes,
promocoes, recrutamento, whitelist), esse trecho deveria sair para um
`_logger.py` próprio. É um trabalho pequeno e de baixo risco, bom candidato para
a próxima leva.

---

## Como manter isto honesto

Sempre que uma pendência for resolvida, atualize dois lugares:

1. Este arquivo, tirando o item da lista.
2. `ferramentas/guardiao.py`, removendo a regra de
   `REGRAS_QUE_AINDA_NAO_DERRUBAM` para que ela passe a reprovar regressões.

O segundo passo é o que importa: enquanto a regra não reprovar, nada impede que
a dívida volte a crescer.
