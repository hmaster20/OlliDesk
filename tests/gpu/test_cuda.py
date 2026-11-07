import llama_cpp

print("Версия:", llama_cpp.__version__)
print("GPU offload поддерживается:", llama_cpp.llama_supports_gpu_offload())

# Дополнительно — какие бэкенды скомпилированы
print("Доступные бэкенды:", llama_cpp.llama_print_system_info().decode(errors="replace"))
