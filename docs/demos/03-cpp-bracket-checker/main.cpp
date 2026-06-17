#include <iostream>
#include <string>
#include <stack>

bool is_matched(const std::string& s) {
    std::stack<char> st;
    for (char c : s) {
        if (c == '(' || c == '[' || c == '{') {
            st.push(c);
        } else if (c == ')' || c == ']' || c == '}') {
            if (st.empty()) return false;
            char top = st.top();
            if ((c == ')' && top != '(') ||
                (c == ']' && top != '[') ||
                (c == '}' && top != '{')) {
                return false;
            }
            st.pop();
        }
    }
    return st.empty();
}

int main() {
    std::string line;
    std::getline(std::cin, line);
    if (is_matched(line)) {
        std::cout << "matched" << std::endl;
    } else {
        std::cout << "not matched" << std::endl;
    }
    return 0;
}
