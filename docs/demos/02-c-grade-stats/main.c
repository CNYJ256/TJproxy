#include <stdio.h>
#include <limits.h>

int main(void) {
    int n;
    if (scanf("%d", &n) != 1) {
        printf("average=0.0\nmax=0\nmin=0\n");
        return 0;
    }
    if (n <= 0) {
        printf("average=0.0\nmax=0\nmin=0\n");
        return 0;
    }
    int sum = 0;
    int max = INT_MIN;
    int min = INT_MAX;
    for (int i = 0; i < n; i++) {
        int score;
        if (scanf("%d", &score) != 1) {
            break;
        }
        sum += score;
        if (score > max) max = score;
        if (score < min) min = score;
    }
    double avg = (double)sum / n;
    printf("average=%.1f\nmax=%d\nmin=%d\n", avg, max, min);
    return 0;
}
