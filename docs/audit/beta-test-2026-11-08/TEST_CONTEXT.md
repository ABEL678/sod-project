# Stream-CAD бэта-тестирование — контекст сессии (ЗАВЕРШЕНО 2026-11-08)

## Итоги
- Прод-тест stream-cad.ru (auditor) выполнен полностью, ИИ-контур включён пользователем
  в .env в ходе сессии (AI_CHECKS_ENABLED=True + LLM orcarouter).
- Код: github.com/ABEL678/sod-project @ c125f98, локальная копия /home/z/my-project/sod-project
  (с наложенными патчами, git diff там же).
- Локальное воспроизведение ИИ-контура: scripts/local_repro.py (SQLite, сид, прогон правил,
  confirm, preview, отчёт PDF/Word, негативные сценарии) — всё 200.
- Итоговые артефакты в /home/z/my-project/download/:
  - Отчет_бэта-теста_Stream-CAD.docx / .pdf (итоговый отчёт, 11 стр.)
  - stream-cad_beta_patches.diff (10 файлов, +139/−24)
  - screenshots/ (8 ключевых скриншотов)

## Дефекты (см. отчёт, раздел 4)
B1 ИИ-контур выключен (устранён пользователем) | B2 500 отчёта ГИПу модель GLM
(llm_http.py get_llm_provider: timeout 60×3 > таймаут gunicorn ~30с) → патч A
B3 пустая история отчётов при reload (?tab=ai-report, нет reports в контексте
project_detail) → патч B | B4 загрузка в ИРД/ПД/РД → 400 «Не выбран раздел»
(app.js currentUploadTarget + views.py:2473) → патч F
B5 {# stage2 … #} многострочный комментарий утекает в HTML (_kb.html:45) → патч C
B6 «6 замечанией» (registry.html:17, Django pluralize не умеет 3 формы) → патч E
B7 пусто под «Например:» (нет example_questions) → патч B
B8 kb-вьюхи без _ensure_enabled → патч G
B9 остаточные 500: кроп >5МБ (create_issue ValueError), rotation='abc' → патч H
B10 «2 открытых замечаний» грамматика → патч D

## Заметки для следующих сессий
- Ключ LLM засвечен в чате — рекомендована ротация.
- Модель deepseek на orcarouter отвечает ошибкой → «Резюме недоступно» (проверить имена моделей).
- Сертификат прод — Russian Trusted CA (в тестовом браузере --ignore-certificate-errors).
- Тестовые данные на проде удалены (замечания 8-11, стандарт KB #1, проект 5 и док 16 — в корзине).
- Residue: CheckRun по документу 2 (findings в статусах подтверждено/отклонено), 3 отчёта ГИПу.
- agent-browser: прод требует --args "--ignore-certificate-errors"; processes умирают между
  bash-вызовами, демон agent-browser живёт.
