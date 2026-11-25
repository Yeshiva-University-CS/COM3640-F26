# Assignment 3: Flipping Huge BMP Images

COM-3640 Programming Languages

Write a library and a program that mirror a BMP image horizontally, vertically, or both, and convert it from any supported format (1, 4, 8, 16, 24 or 32 bits per pixel) to grayscale, 16, 24 or 32 bits. Images can be up to 100,000 × 100,000 pixels (10 to 40 GB), but your code may use only a small, fixed amount of memory: each call gets a memory budget in bytes (100,000 by default, and as little as 4096), so an image never fits in memory at once. Flips can also be done in place, changing the file itself instead of writing a second one.

## What this assignment is about

Imperative and systems programming. You manage memory yourself; you reach storage through an interface modeled on the POSIX system calls `pread` and `pwrite`; and you decide how the data moves between them. The tests check that your output is correct and that you stayed within your memory budget, and an I/O tracker measures how efficiently you moved the data. How you meet those constraints is up to you: there is no single right design.

## Setup

Install Rust from [rustup](https://rustup.rs/). Then, in this folder:

```
cargo test                                                   # the public tests (failing until you write the code) and yours
cargo test --release --test bench -- --nocapture             # your I/O cost next to the reference's
cargo test --release --test bench -- --ignored --nocapture   # the full-size tests: minutes
mkdir out
cargo run --release -- --memory 4096 hv images/f24.bmp out/f24.hv.bmp
```

## The program

```
bmpflip [--memory BYTES] <h|v|hv> <input.bmp> <output.bmp>
bmpflip [--memory BYTES] --in-place <h|v|hv> <file.bmp>
bmpflip [--memory BYTES] --to <gray|16|24|32> [h|v|hv] <input.bmp> <output.bmp>
```

- `h` mirrors the picture horizontally, `v` vertically, and `hv` both ways. `--in-place` changes the file itself.
- `--to` converts the input, whatever its format (1, 4, 8, 16, 24 or 32 bits per pixel), to one of four output formats: `gray` (8-bit grayscale), `16` (16-bit 565), `24` or `32`. There is no 1- or 4-bit output. It flips as well if an operation is given, and cannot be combined with `--in-place`.
- `--memory` is the budget passed to the library: at least 4096 bytes; 100000 if not given.
- On success the exit code is 0. Otherwise print a message of your choice to stderr and exit with:
  - 1: the arguments are wrong: their number, the operation, the format, or the memory budget
  - 2: the input (in place: the file) cannot be opened or read
  - 3: the input is not a BMP file (`Error::NotBmp`)
  - 4: the BMP format is not supported (`Error::Unsupported`)
  - 5: the file is truncated (`Error::Truncated`)
  - 6: the output cannot be created or written
- For exit codes 1 to 5, leave no output file behind, and do not change the file of an in-place flip.
- If the output names the input file, either refuse with exit code 1 and leave the file alone, or flip it correctly.

## The library

`src/lib.rs` declares `Op`, `Format`, `Error`, `Info`, `MIN_MEMORY`, `read_info`, `flip`, `flip_in_place` and `convert`. Keep the name and signature of everything `pub`: the tests call them. Everything else is yours to design, in `lib.rs` or in modules of your own. `src/storage.rs` defines the `Storage` trait and implements it for files. Do not edit it: I replace it when grading.

Rules:

- All I/O goes through `Storage`; `main.rs` opens the files.
- Memory: the heap your code holds, counted from the start of a call to `flip`, `flip_in_place` or `convert`, never exceeds `memory` bytes. The tests count every allocation, and they run your code on a thread with a 128 KB stack, so a big array on the stack crashes.
- `flip_in_place` uses no storage but the file. In the tests, the output storage of `flip` and `convert` is write-only.
- Nothing is written unless `read_info` would succeed on the input; for `convert`, unless checks 9 and 10 succeed too.
- I/O errors become `Error::Read` (from the input, or reading the file in place) or `Error::Write` (from the output, or writing the file in place). `read_at` returning 0 before the data you need has been read means `Error::Truncated`. No input and no storage behavior makes your code panic or loop forever.
- Write tests of your own in new files in `tests/`; they can use the harness in `tests/common/`. When I grade, I replace `tests/`, `images/` and `expected/` with my copies: your test files are not run, and changes to the given ones have no effect.
- I replace `Cargo.toml` too, so no dependencies are available: use the standard library only.

Code rules. Breaking any of them makes the grade 0. They apply to your code in `src/`, not to your tests in `tests/`; I check the source text, leaving out comments:

- No `unsafe`: keep `#![forbid(unsafe_code)]` at the top of `lib.rs`, and write no `unsafe` in `main.rs` either.
- No files and no threads outside `main.rs`: no `std::fs`, `File`, `OpenOptions` or `std::thread` in the library.

## The file format

Background on BMP files, their headers and their pixel formats:

- [BMP file format](https://en.wikipedia.org/wiki/BMP_file_format) (Wikipedia): an overview of the whole file, every header version, color tables, bit fields and each bit depth.
- [Bitmap storage](https://learn.microsoft.com/en-us/windows/win32/gdi/bitmap-storage) (Microsoft): how the headers, color table and pixel array are laid out.
- Microsoft's header references: [BITMAPFILEHEADER](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-bitmapfileheader), [BITMAPINFOHEADER](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-bitmapinfoheader) (the basic DIB header) and [BITMAPV5HEADER](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-bitmapv5header) (the newest one).

Learn the layout from these: the file header, the DIB header versions, where the pixel array starts, how rows are ordered and padded, how pixels of each bit depth are packed, the color table, and the bit-field masks. This handout gives only what the assignment itself decides; where a source differs, the handout decides.

- Supported: the DIB headers, bit depths and compressions allowed by the checks under [Invalid files](#invalid-files); nothing else.
- Files can be far larger than 4 GB. The file-size field cannot hold such sizes, so it is never checked or relied on.
- You never need a pixel's color: to flip a picture, a pixel is a group of bits that moves as a whole. Everything outside the pixel array (the rest of the headers, color tables, bit masks, and anything after the pixel array) stays as it is.

### The output

- The output is the input with the rows of the pixel array rearranged; every byte outside the pixel array is unchanged.
- In the output, the padding at the end of each row is zero, and so are the unused low bits of a row's last byte (1- and 4-bit files), even where the input has something else there (`images/f24-dirty-padding.bmp`). This holds for in-place flips too, for every row.

## Invalid files

Detect these and fail with the error given. A file gets the error of the first check it fails, in this order. The structures and fields named here are described in the documentation linked under [The file format](#the-file-format).

`read_info`, `flip`, `flip_in_place` and `convert`:

1. The file is too short to hold the file header and the DIB header's size field, or does not start with the BMP signature: `NotBmp`.
2. The DIB header is not a `BITMAPINFOHEADER`, `BITMAPV2INFOHEADER`, `BITMAPV3INFOHEADER`, `BITMAPV4HEADER` or `BITMAPV5HEADER` (judged by its size field): `Unsupported`.
3. The file is too short to hold the whole DIB header: `NotBmp`.
4. The width is 0 or less, or the height is 0: `NotBmp`.
5. The bits per pixel are not 1, 4, 8, 16, 24 or 32: `Unsupported`.
6. The compression is neither `BI_RGB` nor `BI_BITFIELDS`, or it is `BI_BITFIELDS` with other than 16 or 32 bits per pixel: `Unsupported`.
7. The pixel array starts before the end of the DIB header: `NotBmp`.
8. The pixel array (every row, padded as the format requires) runs past the end of the file: `Truncated`.

`convert` only, after checks 1 to 8:

9. 1, 4 or 8 bits per pixel: the colors-used field asks for more colors than that many bits can index: `Unsupported`. A color table that runs past the start of the pixel array: `NotBmp`.
10. `BI_BITFIELDS`: a pixel array that starts before the end of the three color masks: `NotBmp`. A mask that is 0, is not a single run of consecutive bits, or (16 bits per pixel) does not fit in 16 bits: `Unsupported`. Masks may overlap.

Nothing else is checked.

## Conversion

`convert` decodes every pixel to a color (red, green, blue, each 0 to 255) and encodes it in the output format. Output row `r`, pixel `x` comes from the input pixel the operation (if any) puts there, exactly as for a flip.

### Conversion rules

Learn from the documentation how each input format stores a pixel: color-table indices, the default 16-, 24- and 32-bit layouts, and bit-field masks. The documentation does not say how to convert between formats, and other sources use different methods (shifting instead of rounding, bit replication, other gray weights) that give different bytes. You must use the method in [Converting colors](CONVERSION.md), which defines every conversion rule and works through examples: the tests check every output byte against it.

### The output file

Every converted file has exactly this layout, whatever the input's headers held:

- the file header, with the file size (0 if it does not fit in its field), zero in the reserved fields, and the offset of the pixel array
- a `BITMAPINFOHEADER`: the width; the height, with the input's sign; planes 1; bits per pixel 8, 16, 24 or 32; compression `BI_BITFIELDS` for 16 bits, otherwise `BI_RGB`; the size of the pixel array (0 if it does not fit in its field); 2835 pixels per meter in both directions; colors used 256 for gray, otherwise 0; colors important 0
- for gray, a color table of 256 entries, entry `i` having red, green and blue `i`; for 16 bits, the masks `0xF800`, `0x07E0` and `0x001F`
- the pixel array, rows padded with zeroes as usual, and nothing after it

## Grading

- 5 points: `cargo clippy --all-targets -- -D warnings` reports nothing.
- 15 points: the public tests in `tests/` (`cli.rs`, `convert.rs`, `flip.rs`, `io.rs`), including every test of invalid files.
- 35 points: hidden tests of valid files like the public ones, with other sizes, memory budgets, headers, color tables and bit masks, and storage that misbehaves.
- 15 points: hidden full-size tests: a 100,000 × 100,000 image, 100,000-pixel-wide images flipped in place, and a 100,000-pixel-wide conversion.
- 30 points: I/O efficiency. On hidden scenarios like those in `tests/bench.rs`, your I/O cost is compared with a basic solution.

Breaking any of the code rules under [The library](#the-library) makes the grade 0, whatever the tests say: the tests cannot measure such code fairly.

## The tests

- `tests/common/mod.rs` is the harness: simulated files that compute their contents on demand (so a 30 GB file needs no disk), an output that checks each write as it arrives, storage that misbehaves as `pread` may, and the memory meter and the I/O tracker. Read it to see exactly what is measured.
- `flip.rs`: every bit depth, both modes, rows that fit in memory, in half of it, or not at all, and several budgets.
- `convert.rs`: every kind of input to every format, with and without flips, short and long rows, checks 9 and 10 and their order, and failing storage.
- `io.rs`: short and interrupted calls, failing storage, a file shorter than it claims, `read_info`, files to reject without writing, and the order of checks 1 to 8.
- `cli.rs`: the program on real files: `images/` (the same portrait in every supported format; `expected/` holds the correct outputs, conversions included) and `images/bad/` (`bad/exit-codes.txt` gives the exit code for each).
- `bench.rs`: the I/O cost table and the full-size tests.
- Your own tests: add files of your own to `tests/`; `cargo test` runs them with the rest. I replace `tests/` when grading, so they are for you only.

## Submission

Check your work into your Git repo in the PL-COM3640 branch, in an `/assignment-3` folder: `src/` with all your code, and `tests/` if you wrote tests of your own. You are free to check in the rest of the folder you were given too, so that you have a full working project; however, I grade only your code in `src/`, and I replace `Cargo.toml`, `src/storage.rs`, `tests/`, `images/` and `expected/` with my copies. Do **not** submit `target/` or compiled programs.
