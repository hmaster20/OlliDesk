# структура задач (описание, не код)

Каждая задача — каталог с repo/, task.md, tests/, expected.patch. Создаём 4 задачи (по одной на язык), остальные — пользователь добавляет вручную.

```md
tests/bench/tasks/
├── py_fix_parser/
│   ├── repo/
│   │   └── parser.py
│   ├── tests/
│   │   └── test_parser.py
│   ├── task.md
│   └── expected.patch
├── py_add_function/
│   ├── repo/
│   │   └── utils.py
│   ├── tests/
│   │   └── test_utils.py
│   ├── task.md
├── go_fix_offbyone/
│   ├── repo/
│   │   └── main.go
│   ├── repo/main_test.go
│   ├── task.md
└── ts_add_type/
    ├── repo/
    │   └── index.ts
    ├── tests/
    │   └── index.test.ts
    └── task.md
```
