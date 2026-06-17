#include <stdio.h>
#include <limits.h>

int main() {
    int n;
    scanf("%d", &n);

    if (n <= 0) {
        printf("n must be positive\n");
        return 1;
    }

    int score;
    int sum = 0;
    int max = INT_MIN;
    int min = INT_MAX;

    for (int i = 0; i < n; i++) {
        scanf("%d", &score);
        sum += score;
        if (score > max) max = score;
        if (score < min) min = score;
    }

    double avg = (double)sum / n;
    printf("average: %.2f\n", avg);
    printf("max: %d\n", max);
    printf("min: %d\n", min);

    return 0;
}
