### COM-3640 Programming Languages
# Assignment #1: Getting Comfortable with Languages

## Background and Motivation

This assignment gives you the opportunity to install, write, run, and debug programs in several languages, and to compare their syntax and semantics by implementing the exact same program in each of them.

You may use AI tools such as Claude or ChatGPT, Gemini, or official language documentation to help you overcome any issue or blocker you encounter. You <u>may not</u> have a friend write or debug code for you. A friend may point you to a relevant video, webpage, or documentation page. A friend <u>may not</u> share their code, their AI prompts, or their AI conversations with you.

## The Program: Word-Frequency Analyzer

You are to write a command-line program that reads a text file and reports statistics about the words it contains.

**Input:** The program accepts two command-line arguments:

1. The path to a text file (required).
2. `N`, the number of top words to display (default `10` if omitted).

**Normalization rules** (apply these before counting, so everyone's implementations agree on what counts as "the same word"):

- Convert all letters to lowercase.
- Strip any character that is not a letter or an apostrophe.
- Split on whitespace.

**Output:** The program prints:

- Total word count.
- Distinct word count.
- The longest word (and its length). If there is a tie for longest, print the first one alphabetically ordered.
- The top `N` words by frequency, most frequent first. If two or more words share the same frequency, the order is determined alphabetically.

**Sample run**, given a file `sample.txt` containing:

```
The quick brown fox jumps over the lazy dog. The dog barks at the astonishingly quick fox.
```

Output the following when run:

```
$ wordstats sample.txt 5
Total words: 17
Distinct words: 11
Longest word: astonishingly (14 letters)

Top 5 words:
1. the             (4)
2. dog             (2)
3. fox             (2)
4. quick           (2)
5. astonishingly   (1)
```

(`dog`, `fox`, and `quick` are tied at 2 occurrences each; the order shown is alphabetical)

**Error handling:** If the file cannot be found or read, print:

```
Error: cannot read file "<path>"
```

and exit with a non-zero status. Do not crash with a raw language exception/stack trace.

## Programming Languages

You will implement this identical specification in **all five** of the following languages:

- Rust
- Go
- C++
- Ruby
- JavaScript (Node.js)

## Standard Library Only

You may use only the standard library and core language features that ship with each language's official distribution. Do **not** use any third-party crates (Rust), external packages (Go beyond its standard library), gems (Ruby), npm packages (JavaScript), or external libraries (use the C++ Standard Library only, no Boost, no third-party JSON/string-utility libraries, etc.). If you are unsure whether something counts as standard library for your language, ask before using it.

## IDE and Debugging

You must use a **general-purpose, extensible code editor such as Visual Studio Code or Cursor** for all editing, running, and debugging in this assignment. **JetBrains IDEs (CLion, GoLand, RubyMine, WebStorm, RustRover, IntelliJ, etc.) may not be used for any part of the assignment**, including the debugging demonstration.

You are responsible for identifying and installing the appropriate **language extensions and development tools** needed to configure VS Code or Cursor for each language.

For **each** of the five languages, set a breakpoint inside your word-counting loop, stop execution after a few words have been processed, and take a screenshot showing the debugger paused there with the current word/count tally visible in a variables or watch panel.

## Submission

Check your work into your Git repo in the PL-COM3640 branch, with the following directory structure:

```
/assignment-1/wordstats/rust/
/assignment-1/wordstats/go/
/assignment-1/wordstats/cpp/
/assignment-1/wordstats/ruby/
/assignment-1/wordstats/javascript/
```

Each language subfolder must contain:

- Your source file(s).
- A short `README.txt` with the exact commands to build (if applicable) and run your program from the command line
- Your debugger screenshot for that language (e.g. `debug-rust.png`).

Do not submit compiled binaries, `node_modules`, `target/`, or other generated build artifacts.
