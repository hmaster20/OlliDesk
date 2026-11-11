package main

// Sum возвращает сумму чисел от 1 до n.
func Sum(n int) int {
	total := 0
	for i := 1; i < n; i++ { // BUG: должно быть i <= n
		total += i
	}
	return total
}