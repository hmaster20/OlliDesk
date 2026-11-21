package main

import "testing"

func TestSum(t *testing.T) {
	cases := []struct{ n, want int }{
		{1, 1},
		{5, 15},
		{10, 55},
	}
	for _, c := range cases {
		got := Sum(c.n)
		if got != c.want {
			t.Errorf("Sum(%d) = %d, want %d", c.n, got, c.want)
		}
	}
}