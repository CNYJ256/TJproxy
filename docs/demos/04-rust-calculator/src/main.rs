fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn sub(a: i32, b: i32) -> i32 {
    a - b
}

fn mul(a: i32, b: i32) -> i32 {
    a * b
}

fn div(a: i32, b: i32) -> Option<i32> {
    if b == 0 {
        None
    } else {
        Some(a / b)
    }
}

fn main() {
    println!("Rust Calculator");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(add(2, 3), 5);
        assert_eq!(add(-1, 1), 0);
        assert_eq!(add(0, 0), 0);
    }

    #[test]
    fn test_sub() {
        assert_eq!(sub(5, 3), 2);
        assert_eq!(sub(0, 5), -5);
        assert_eq!(sub(-3, -7), 4);
    }

    #[test]
    fn test_mul() {
        assert_eq!(mul(4, 3), 12);
        assert_eq!(mul(-2, 5), -10);
        assert_eq!(mul(0, 100), 0);
    }

    #[test]
    fn test_div() {
        assert_eq!(div(10, 2), Some(5));
        assert_eq!(div(7, 3), Some(2));
        assert_eq!(div(-8, 4), Some(-2));
    }

    #[test]
    fn test_div_by_zero() {
        assert_eq!(div(5, 0), None);
        assert_eq!(div(-3, 0), None);
        assert_eq!(div(0, 0), None);
    }
}
