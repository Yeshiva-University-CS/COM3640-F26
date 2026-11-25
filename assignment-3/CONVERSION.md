# Converting colors

This document defines how `convert` turns a pixel in one format into a pixel in another, with worked examples. It is part of the assignment: the [handout](README.md) refers to it for every conversion rule, and the tests check every output byte against it.

**Use this method only.** There are several ways to convert colors, and other sources (including Microsoft's documentation) use different ones: shifting bits instead of rounding, bit replication, other gray weights. They give different bytes, and the tests check every output byte against the method here, so follow these formulas even where another source differs.

Every conversion has two steps, whatever the formats:

1. Decode the input pixel to a color: red, green and blue, each 0 to 255.
2. Encode that color in the output format.

So you never convert 565 to gray, or 4-bit to 32-bit, directly: everything goes through 8-bit red, green and blue. That way each input format needs one decoder and each output format one encoder, instead of one routine per pair.

All arithmetic is on integers: `/` divides and drops the remainder, `>> k` shifts right by `k` bits (divides by 2^k and drops the remainder), and `<< k` shifts left (multiplies by 2^k).

## Formats

`convert` reads every format the checks in the handout accept, and writes four. Input means `convert` must read the format; output means `--to` can write it.

| Bits per pixel | Layout | Input | Output |
|---|---|:---:|:---:|
| 1 | color table | ✓ | |
| 4 | color table | ✓ | |
| 8 | color table, any colors | ✓ | |
| 8 | color table of 256 grays | ✓ | ✓ |
| 16 | `BI_RGB`: 555 | ✓ | |
| 16 | `BI_BITFIELDS`: any masks | ✓ | |
| 16 | `BI_BITFIELDS`: 565 | ✓ | ✓ |
| 24 | `BI_RGB`: one byte per channel | ✓ | ✓ |
| 32 | `BI_RGB`: one byte per channel and an unused byte | ✓ | ✓ |
| 32 | `BI_BITFIELDS`: any masks | ✓ | |

There is no 1- or 4-bit output, and no 8-bit output in color: writing one would mean choosing which 256 colors to keep, which this assignment does not ask for. Flips handle every input format and keep it as it is.

## Step 1: from a pixel to red, green and blue

### Pixels that are indices (1, 4 and 8 bits)

In these images a pixel is not a color but a number: an index into the color table, a list of colors stored in the file (the documentation shows where). Each entry holds one color's red, green and blue, already 0 to 255, so decoding is a lookup: a pixel with value `i` has the color in entry `i`.

How many entries the table has is given by a field in the DIB header, the colors-used field (`biClrUsed` in Microsoft's documentation, which also says what a value of 0 means). The table can be shorter than the pixels can index, and then some pixel values have no color:

- A 4-bit pixel can hold any value from 0 to 15. Suppose the image's colors-used field is 12: its color table has 12 entries, numbered 0 to 11.
- A pixel with value 7 has the color in entry 7: if entry 7 holds (200, 30, 40), the pixel is (200, 30, 40).
- A pixel with value 13 points past the end of the table: there is no entry 13. Such a pixel is black, (0, 0, 0). So are pixels with values 12, 14 and 15. Real files rarely contain such pixels, but the tests do.

### Pixels that hold their channels (16, 24 and 32 bits)

Here the pixel itself holds the color: its bits are divided into groups, one group per channel. Which bits belong to which channel depends on the format:

- With `BI_BITFIELDS`, the file stores a mask for each channel (the documentation shows where).
- With `BI_RGB`, the file stores no masks: each bit depth has a default layout, which the documentation gives. It helps to write that layout as three masks of your own; then the same code handles both cases.
- In a 24- or 32-bit `BI_RGB` pixel each channel is a whole byte, so its width is 8 bits and the scaling in (b) below leaves it unchanged.

Getting a channel out takes two steps: (a) isolate its bits, then (b) scale them to 0 to 255.

**(a) Isolate the channel.** A mask is a number whose 1 bits mark the channel's bits within the pixel. Take the 16-bit pixel `0xA285`, with the masks `0xF800` (red), `0x07E0` (green) and `0x001F` (blue). In binary:

```
pixel        1010 0010 1000 0101    0xA285
red mask     1111 1000 0000 0000    0xF800
green mask   0000 0111 1110 0000    0x07E0
blue mask    0000 0000 0001 1111    0x001F
```

For green:

1. `pixel & mask` keeps the pixel's bits where the mask has a 1 and clears all the others: `0xA285 & 0x07E0` = `0000 0010 1000 0000` = `0x0280`.
2. The channel's bits are still where they sat in the pixel: the mask has five 0 bits below its lowest 1, so they start 5 places up. Shift them down: `0x0280 >> 5` = `10100` in binary = **20**.
3. The channel's width, `n`, is the number of 1 bits in the mask: here six, so `n` = 6.

The same steps for the other two channels:

- red: `(0xA285 & 0xF800) >> 11` = `10100` = **20**; the mask has five 1 bits, so `n` = 5
- blue: `(0xA285 & 0x001F) >> 0` = `00101` = **5**; `n` = 5

Reading the pixel's bits in groups of 5, 6 and 5 shows the same thing: `10100 010100 00101` is red 20, green 20, blue 5.

**(b) Scale the channel to 0 to 255.** A channel of `n` bits holds a value `c` from 0 to `m`, where `m = 2^n − 1` is the largest value `n` bits can hold: 31 for 5 bits, 63 for 6, 255 for 8. Its 0 must become 0, its `m` must become 255, and the values between must be spread evenly. The exact result is `c × 255 / m`, which is usually not a whole number, so the rule rounds it to the nearest one:

```
(c × 255 + m / 2) / m
```

Why adding `m / 2` rounds: integer division drops the remainder, so on its own it always rounds down. Adding half of the divisor first lifts every value whose fraction is one half or more past the next whole number, so dropping the remainder then gives the nearest whole number. It is the integer version of adding 0.5 before cutting off the fraction. `n` is the width of the channel, not of the pixel.

The pixel above:

- red, 5 bits (`m` = 31, `m / 2` = 15): exactly, 20 × 255 / 31 = 164.52, which rounds to 165. In integers: (20 × 255 + 15) / 31 = 5115 / 31 = **165**
- green, 6 bits (`m` = 63, `m / 2` = 31): exactly 80.95, which rounds to 81. In integers: (20 × 255 + 31) / 63 = 5131 / 63 = 81 remainder 28, so **81**
- blue, 5 bits: exactly 41.13, which rounds to 41. In integers: (5 × 255 + 15) / 31 = 1290 / 31 = 41 remainder 19, so **41**

So `0xA285` is the color **(165, 81, 41)**. Red and green were both 20, yet they give different values, because the channels have different widths: 20 out of 31 is brighter than 20 out of 63.

More values, to check your code against:

- 5 bits, 31 (the largest): (31 × 255 + 15) / 31 = **255**; 5 bits, 0: **0**
- 1 bit, 1: `m` = 1, `m / 2` = 0, so (255 + 0) / 1 = **255**
- 8 bits, 200: `m` = 255, `m / 2` = 127, so (51000 + 127) / 255 = 200 remainder 127, so **200**; an 8-bit channel is unchanged
- 10 bits, 512 (a mask such as `0x3FF00000`): `m` = 1023, `m / 2` = 511, so (130560 + 511) / 1023 = 131071 / 1023 = 128 remainder 127, so **128**
- 10 bits, 1023: (260865 + 511) / 1023 = 261376 / 1023 = 255 remainder 511, so **255**

The formula works for any width, including masks wider than 8 bits. Watch the size of `c × 255`: a mask can be up to 32 bits wide, so the product can need more than 32 bits.

**Why not just shift?** Some sources widen a 5-bit channel with `c << 3`. For 20 that gives 160, not 165, and the largest value, 31, becomes 248, so white is never quite white. Dividing without the rounding term, (20 × 255) / 31, gives 164. Both are reasonable choices; neither is this assignment's, and the tests would mark them wrong.

Bits that belong to no color channel (an unused byte or bit, or an alpha channel) are ignored: they never affect the color.

## Step 2: from red, green and blue to the output

Take the color (165, 81, 41) from above. In hexadecimal it is (`0xA5`, `0x51`, `0x29`), which is how its bytes appear in a file.

### 24 and 32 bits

The channels are stored whole, one byte each, in the order the documentation gives for `BI_RGB`. A 32-bit pixel has a fourth, unused byte, which must be 0.

- 24 bits: the bytes `29 51 A5`
- 32 bits: the bytes `29 51 A5 00`

### 16 bits (565)

Each channel is first narrowed from 0 to 255 to its width in the output: 5 bits for red and blue (0 to 31), 6 for green (0 to 63). This is the reverse of the scaling in step 1: the exact result is `c × m / 255`, rounded to the nearest whole number in the same way, by adding half of the divisor:

```
(c × m + 127) / 255        m = 31 for red and blue, 63 for green
```

- red: exactly 165 × 31 / 255 = 20.06, so 20. In integers: (165 × 31 + 127) / 255 = 5242 / 255 = 20 remainder 142, so **20**
- green: exactly 20.01, so 20. In integers: (81 × 63 + 127) / 255 = 5230 / 255 = 20 remainder 130, so **20**
- blue: exactly 4.98, so 5. In integers: (41 × 31 + 127) / 255 = 1398 / 255 = 5 remainder 123, so **5**

The blue channel shows why the rounding term matters: without it, 41 × 31 / 255 = 1271 / 255 = 4.

Then the three values are packed into one 16-bit number, each in the bits its output mask marks: red in the top 5 bits (mask `0xF800`), green in the middle 6 (`0x07E0`), blue in the low 5 (`0x001F`). Shifting left moves a value up into place, and `|` combines them:

```
red   20 = 10100     20 << 11 = 1010 0000 0000 0000
green 20 = 010100    20 << 5  = 0000 0010 1000 0000
blue   5 = 00101     5        = 0000 0000 0000 0101
                     combined = 1010 0010 1000 0101 = 0xA285 = 41605
```

A 16-bit value is stored low byte first (little-endian), so the file holds the bytes `85 A2`. That is the pixel we started from: converting this 565 pixel to 565 gives it back unchanged.

### Gray

A gray pixel is one byte: an index into the gray color table, whose entry `i` is (`i`, `i`, `i`). So the byte is simply the color's brightness, from 0 (black) to 255 (white):

```
(77 × red + 150 × green + 29 × blue + 128) >> 8
```

For (165, 81, 41):

- 77 × 165 = 12705
- 150 × 81 = 12150
- 29 × 41 = 1189
- sum 26044; plus 128 is 26172; 26172 >> 8 = 26172 / 256 = 102 remainder 60, so **102** (the byte `66`)

Where the numbers come from:

- Eyes are far more sensitive to green than to blue, so brightness is a weighted sum, not an average. The standard weights, from the television standard BT.601, are 0.299 for red, 0.587 for green and 0.114 for blue. For this color the exact brightness is 0.299 × 165 + 0.587 × 81 + 0.114 × 41 = 101.56, which rounds to 102.
- To stay in integers, the weights are multiplied by 256: 76.5, 150.3 and 29.2, rounded to 77, 150 and 29. These add up to exactly 256, so dividing the sum by 256 (`>> 8` is the same as `/ 256`) brings it back to 0 to 255, and white stays white: (256 × 255 + 128) >> 8 = **255**.
- Adding 128 (half of 256) before the shift rounds to the nearest whole number instead of rounding down, as in the other formulas.

The weights at work, on the three pure colors:

- red (255, 0, 0): (77 × 255 + 128) >> 8 = 19763 / 256 = **77**
- green (0, 255, 0): (150 × 255 + 128) >> 8 = 38378 / 256 = **149**
- blue (0, 0, 255): (29 × 255 + 128) >> 8 = 7523 / 256 = **29**

A plain average would make all three 85, and would turn (165, 81, 41) into (165 + 81 + 41) / 3 = 95 instead of 102.

## Putting it together

**A bit-field pixel.** The 16-bit pixel `0xA285`, with the masks `0xF800`, `0x07E0` and `0x001F`:

- decode: fields (20, 20, 5), widths (5, 6, 5), color (165, 81, 41)
- `--to gray`: the byte `66` (102)
- `--to 16`: the bytes `85 A2`
- `--to 24`: the bytes `29 51 A5`
- `--to 32`: the bytes `29 51 A5 00`

**A color-table pixel.** A 4-bit image whose colors-used field is 12, and whose entry 7 holds (200, 30, 40). Table colors are already 0 to 255, so there is nothing to scale.

- pixel 7: color (200, 30, 40)
  - `--to gray`: 77 × 200 + 150 × 30 + 29 × 40 + 128 = 15400 + 4500 + 1160 + 128 = 21188; 21188 / 256 = 82 remainder 196, so the byte `52` (82)
  - `--to 16`: red (200 × 31 + 127) / 255 = 6327 / 255 = 24; green (30 × 63 + 127) / 255 = 2017 / 255 = 7; blue (40 × 31 + 127) / 255 = 1367 / 255 = 5; packed, 24 << 11 | 7 << 5 | 5 = `0xC0E5`, so the bytes `E5 C0`
  - `--to 24`: the bytes `28 1E C8`
  - `--to 32`: the bytes `28 1E C8 00`
- pixel 13: past the end of the table, so black, (0, 0, 0)
  - `--to gray`: `00`; `--to 16`: `00 00`; `--to 24`: `00 00 00`; `--to 32`: `00 00 00 00`

With a flip, only the choice of input pixel changes: output pixel `x` of row `r` comes from the pixel the operation puts there, which then converts exactly as above.

**Checking your work.** `expected/` holds the correct conversion of every sample image to every format, for example `expected/f4.to-gray.bmp` for `images/f4.bmp`. Convert the image yourself and compare the two files byte for byte:

```
cargo run --release -- --to gray images/f4.bmp out/f4.to-gray.bmp
cmp out/f4.to-gray.bmp expected/f4.to-gray.bmp
```

`cmp` prints the first byte that differs, if any (on Windows, `fc /b` lists every byte that differs). The offset of a difference tells you which pixel, and which channel, to work through by hand.

## Further reading

These explain the background. Where they give a different method, use the formulas above.

- [Grayscale](https://en.wikipedia.org/wiki/Grayscale) and [Luma](https://en.wikipedia.org/wiki/Luma_%28video%29) (Wikipedia): why brightness is a weighted sum.
- [Rec. 601](https://en.wikipedia.org/wiki/Rec._601) (Wikipedia): the standard the gray weights come from.
- [Working with 16-bit RGB](https://learn.microsoft.com/en-us/windows/win32/directshow/working-with-16-bit-rgb) (Microsoft): the 555 and 565 layouts; it widens channels with a plain shift, which this assignment replaces with rounding.
