# Інертні приклади структури

Файли ілюструють manifest input/output, два config з consumer-owned edge, plugin settings schema, runtime status та media contract. `reference-input`/`reference-output` не зареєстровані й не мають worker/UI реалізації. Приклади не слід копіювати в production configs як готові ноди. `enabled=false`; жодної автоматичної інсталяції чи запуску.

`manifest-input.json` і `manifest-output.json` показують вміст майбутнього `manifest.json` кожного окремого модуля. Спільний порожній `config.schema.json` описує settings лише для цих прикладів. Це не plugin template з фіктивним working pipeline.

`media-contract.json` демонструє структуру 1080i50 (25 кадр/с) та одного 8-channel audio stream без припущення про labels/remix. Порожні transport descriptors свідомо не є реалізацією SHM і не проходять майбутню transport-specific readiness validation. `state=ready` показує форму документа готового producer, не реальний стан сервера. `node-status.json` — окремий приклад waiting_signal; файли не є синхронним runtime snapshot одного моменту.

UUID, час і параметри вигадані. `$schema` у файлах — editor hint до локальної схеми, дозволений як metadata field; він не визначає runtime schema і не дозволяє завантажувати довільний URL. Loader обирає схему за відомим type/schema_version, а не за цим hint. Локально файли не валідовувалися виконуваним кодом.
