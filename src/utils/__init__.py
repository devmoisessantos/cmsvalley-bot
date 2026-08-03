"""
Traceback (most recent call last):
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
[2026-08-03 10:49:33] [ERROR   ] discord.ui.view: Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
Traceback (most recent call last):
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
    await item.callback(interaction)
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
ERROR:discord.ui.view:Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
Traceback (most recent call last):
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
    await item.callback(interaction)
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
[2026-08-03 10:49:36] [ERROR   ] discord.ui.view: Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
Traceback (most recent call last):
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
    await item.callback(interaction)
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
ERROR:discord.ui.view:Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
Traceback (most recent call last):
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
    await item.callback(interaction)
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
INFO:src.services.plantao_tasks:🔇 verificar_afk TICK — 0 em call ativa
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
ERROR:discord.ui.view:Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
Traceback (most recent call last):
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
[2026-08-03 10:49:51] [ERROR   ] discord.ui.view: Ignoring exception in view <LayoutView timeout=600 children=1> for item <Button style=<ButtonStyle.secondary: 2> url=None disabled=False label='⬅️ Voltar' emoji=None row=None sku_id=None id=None>
    await item.callback(interaction)
Traceback (most recent call last):
  File "/app/src/panels/chamada_panel.py", line 634, in _voltar_para_etapa
  File "/app/.venv/lib/python3.13/site-packages/discord/ui/view.py", line 598, in _scheduled_task
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))
    await item.callback(interaction)
TypeError: _construir_etapa_1() missing 1 required positional argument: 'guild'
"""