//! The test harness. Read it to see what is measured; I grade with my own copy, so changes here have no effect.
//!
//! - `Image` describes a BMP file whose pixels are computed on demand, so a
//!   30 GB file needs no disk and no memory.
//! - `Generated` is a read-only `Storage` holding such a file; `Checked` is an
//!   output `Storage` that compares every write with the correct result and
//!   remembers which bytes were written; `MemFile` is an ordinary file in memory,
//!   used for in-place flips.
//! - `Flaky`, `FailAfter` and `LongerThanItIs` misbehave as `pread` may.
//! - `Checked::converting` and `convert_generated` do the same for `convert`.
//! - Every storage counts its calls and bytes; `cost` turns them into one number.
//! - `run` calls your code on its own thread, with a small stack, and measures
//!   the most heap memory your code held at any moment.
#![allow(dead_code)]

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::collections::BTreeMap;
use std::io;
use std::sync::mpsc;
use std::time::Duration;

use bmpflip::{Format, Op, Storage};

// ---------------------------------------------------------------- memory --

thread_local! {
    static ACTIVE: Cell<bool> = const { Cell::new(false) };
    static CURRENT: Cell<isize> = const { Cell::new(0) };
    static PEAK: Cell<isize> = const { Cell::new(0) };
}

struct Counting;

fn note(delta: isize) {
    let _ = ACTIVE.try_with(|active| {
        if active.get() {
            let now = CURRENT.with(|c| {
                c.set(c.get() + delta);
                c.get()
            });
            PEAK.with(|p| p.set(p.get().max(now)));
        }
    });
}

// SAFETY: forwards every call to the system allocator unchanged.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        note(layout.size() as isize);
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        note(layout.size() as isize);
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        note(new_size as isize - layout.size() as isize);
        unsafe { System.realloc(ptr, layout, new_size) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        note(-(layout.size() as isize));
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

/// Runs `f` with memory counting paused: the harness's own allocations are not yours.
fn harness<T>(f: impl FnOnce() -> T) -> T {
    let was = ACTIVE.with(|a| a.replace(false));
    let result = f();
    ACTIVE.with(|a| a.set(was));
    result
}

/// The stack your code runs on. Big buffers belong on the heap, where they are counted.
pub const STACK: usize = 128 * 1024;

/// How long one call of your code may take in the tests: 600 seconds, or the
/// number of seconds in the environment variable BMPFLIP_TIME_LIMIT.
pub fn time_limit() -> Duration {
    let secs = std::env::var("BMPFLIP_TIME_LIMIT").ok().and_then(|s| s.parse().ok()).unwrap_or(600);
    Duration::from_secs(secs)
}

/// Runs `f` on a thread with a `STACK`-byte stack and returns its result and
/// the peak heap memory it held. Panics if `f` panics or runs past `time_limit()`.
pub fn run<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> (T, usize) {
    let (tx, rx) = mpsc::channel();
    std::thread::Builder::new()
        .stack_size(STACK)
        .spawn(move || {
            CURRENT.with(|c| c.set(0));
            PEAK.with(|p| p.set(0));
            ACTIVE.with(|a| a.set(true));
            let result = f();
            ACTIVE.with(|a| a.set(false));
            let _ = tx.send((result, PEAK.with(Cell::get).max(0) as usize));
        })
        .unwrap();
    match rx.recv_timeout(time_limit()) {
        Ok(done) => done,
        Err(mpsc::RecvTimeoutError::Timeout) => panic!("no result after {:?}: an endless loop?", time_limit()),
        Err(mpsc::RecvTimeoutError::Disconnected) => panic!("your code panicked (see the message above)"),
    }
}

// ---------------------------------------------------------------- images --

#[derive(Clone, Debug)]
pub struct Image {
    pub bpp: u16,
    pub width: u32,
    pub height: u32,
    pub top_down: bool,
    pub dib: u32,
    pub compression: u32,
    /// Bytes between the DIB header and the pixel array (color table, masks, anything).
    pub gap: usize,
    /// Bytes after the pixel array.
    pub trailing: usize,
    /// Non-zero padding and unused bits in the input.
    pub dirty: bool,
    pub seed: u64,
    /// The colors-used field (offset 46): 0 means 2^bpp.
    pub colors: u32,
    /// Bit masks for red, green and blue (offset 54), with compression 3.
    pub masks: Option<[u32; 3]>,
}

fn mix(mut x: u64) -> u64 {
    x ^= x >> 31;
    x = x.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x ^ (x >> 29)
}

impl Image {
    pub fn new(bpp: u16, width: u32, height: u32) -> Image {
        let gap = if bpp <= 8 { 4 << bpp } else { 0 };
        Image { bpp, width, height, top_down: false, dib: 40, compression: 0, gap, trailing: 0, dirty: false, seed: 1, colors: 0, masks: None }
    }
    /// A 16- or 32-bit image with bit masks (compression 3). Set `dib` first.
    pub fn bitfields(self, masks: [u32; 3]) -> Image {
        let gap = if self.dib == 40 { self.gap.max(12) } else { self.gap };
        Image { compression: 3, masks: Some(masks), gap, ..self }
    }
    /// An indexed image whose color table has `colors` entries.
    pub fn colors(self, colors: u32) -> Image {
        Image { colors, gap: 4 * colors as usize, ..self }
    }
    /// The number of color-table entries.
    pub fn palette_len(&self) -> u32 {
        if self.colors == 0 { 1 << self.bpp } else { self.colors }
    }
    /// Color-table entry `i` as (red, green, blue).
    pub fn palette_entry(&self, i: u32) -> [u8; 3] {
        let x = mix(u64::from(i) ^ self.seed << 20 ^ 0xC010);
        [x as u8, (x >> 8) as u8, (x >> 16) as u8]
    }
    pub fn top_down(self) -> Image {
        Image { top_down: true, ..self }
    }
    pub fn dirty(self) -> Image {
        Image { dirty: true, ..self }
    }
    pub fn name(&self) -> String {
        format!(
            "{}x{} {}bpp{}{}{}{}{}",
            self.width,
            self.height,
            self.bpp,
            if self.top_down { " top-down" } else { "" },
            if self.dib != 40 { format!(" dib={}", self.dib) } else { String::new() },
            if self.gap > 0 { format!(" gap={}", self.gap) } else { String::new() },
            if self.trailing > 0 { format!(" trailing={}", self.trailing) } else { String::new() },
            if self.dirty { " dirty" } else { "" }
        )
    }
    pub fn bits(&self) -> u64 {
        u64::from(self.width) * u64::from(self.bpp)
    }
    /// Bytes holding pixels at the start of a row.
    pub fn n(&self) -> u64 {
        self.bits().div_ceil(8)
    }
    pub fn stride(&self) -> u64 {
        self.bits().div_ceil(32) * 4
    }
    pub fn shift(&self) -> u32 {
        (self.n() * 8 - self.bits()) as u32
    }
    pub fn offset(&self) -> u64 {
        14 + u64::from(self.dib) + self.gap as u64
    }
    pub fn pixel_end(&self) -> u64 {
        self.offset() + self.stride() * u64::from(self.height)
    }
    pub fn len(&self) -> u64 {
        self.pixel_end() + self.trailing as u64
    }

    /// The bytes before the pixel array.
    pub fn header(&self) -> Vec<u8> {
        let mut b: Vec<u8> = (0..self.offset()).map(|i| mix(i ^ self.seed << 40) as u8).collect();
        let height = if self.top_down { -(self.height as i32) } else { self.height as i32 };
        b[0..2].copy_from_slice(b"BM");
        b[2..6].copy_from_slice(&(self.len() as u32).to_le_bytes());
        b[10..14].copy_from_slice(&(self.offset() as u32).to_le_bytes());
        b[14..18].copy_from_slice(&self.dib.to_le_bytes());
        b[18..22].copy_from_slice(&self.width.to_le_bytes());
        b[22..26].copy_from_slice(&height.to_le_bytes());
        b[26..28].copy_from_slice(&1u16.to_le_bytes());
        b[28..30].copy_from_slice(&self.bpp.to_le_bytes());
        b[30..34].copy_from_slice(&self.compression.to_le_bytes());
        b[46..50].copy_from_slice(&self.colors.to_le_bytes());
        if let Some(masks) = self.masks {
            for (i, m) in masks.iter().enumerate() {
                b[54 + 4 * i..58 + 4 * i].copy_from_slice(&m.to_le_bytes());
            }
        }
        if self.bpp <= 8 {
            let start = 14 + self.dib as usize;
            for i in 0..self.palette_len() {
                let at = start + 4 * i as usize;
                if at + 4 <= b.len() {
                    let [r, g, bl] = self.palette_entry(i);
                    b[at..at + 4].copy_from_slice(&[bl, g, r, 0]);
                }
            }
        }
        b
    }

    /// Byte `j` of file row `r` of the input.
    pub fn row_byte(&self, r: u64, j: u64) -> u8 {
        self.byte_in_row(r, j, self.n(), 0xFF << self.shift())
    }

    fn byte_in_row(&self, r: u64, j: u64, n: u64, last_mask: u8) -> u8 {
        let x = mix(r.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ j.wrapping_mul(0xC2B2_AE3D_27D4_EB4F) ^ self.seed) as u8;
        if self.dirty || j + 1 < n {
            x
        } else if j + 1 == n {
            x & last_mask
        } else {
            0
        }
    }

    /// Fills `buf` with the input file's bytes from `pos` on. `header` is `self.header()`.
    pub fn fill(&self, header: &[u8], pos: u64, buf: &mut [u8]) {
        let (offset, end, stride, n, mask) = (self.offset(), self.pixel_end(), self.stride(), self.n(), 0xFF << self.shift());
        let mut i = 0;
        while i < buf.len() {
            let p = pos + i as u64;
            if p < offset {
                buf[i] = header[p as usize];
                i += 1;
            } else if p >= end {
                buf[i] = mix(p ^ 0x5555) as u8;
                i += 1;
            } else {
                // a run of pixel-array bytes, row by row
                let (mut r, mut j) = ((p - offset) / stride, (p - offset) % stride);
                let run = ((end - p) as usize).min(buf.len() - i);
                for b in &mut buf[i..i + run] {
                    *b = self.byte_in_row(r, j, n, mask);
                    j += 1;
                    if j == stride {
                        j = 0;
                        r += 1;
                    }
                }
                i += run;
            }
        }
    }

    /// The whole input file. Only for images that fit in memory.
    pub fn bytes(&self) -> Vec<u8> {
        let mut b = vec![0; self.len() as usize];
        self.fill(&self.header(), 0, &mut b);
        b
    }

    /// Pixel `x` of file row `r` of the input, as a number of `bpp` bits.
    fn pixel(&self, r: u64, x: u64) -> u32 {
        let bpp = u64::from(self.bpp);
        if bpp >= 8 {
            let k = bpp / 8;
            (0..k).fold(0, |v, i| v | u32::from(self.row_byte(r, x * k + i)) << (8 * i))
        } else {
            let bit = x * bpp;
            u32::from(self.row_byte(r, bit / 8) >> (8 - bpp - bit % 8)) & ((1 << bpp) - 1)
        }
    }

    /// The color of pixel `x` of file row `r`, as (red, green, blue), following
    /// the handout's rules for each format.
    pub fn rgb(&self, r: u64, x: u64) -> [u8; 3] {
        let v = self.pixel(r, x);
        let scale = |c: u32, bits: u32| -> u8 {
            let max = (1u64 << bits) - 1;
            ((u64::from(c) * 255 + max / 2) / max) as u8
        };
        let field = |m: u32| -> u8 { scale((v & m) >> m.trailing_zeros(), m.count_ones()) };
        match (self.bpp, self.compression) {
            (1 | 4 | 8, _) => if v < self.palette_len() { self.palette_entry(v) } else { [0, 0, 0] },
            (16, 0) => [scale(v >> 10 & 31, 5), scale(v >> 5 & 31, 5), scale(v & 31, 5)],
            (24, _) | (32, 0) => [(v >> 16) as u8, (v >> 8) as u8, v as u8],
            _ => {
                let [mr, mg, mb] = self.masks.expect("bit fields need masks");
                [field(mr), field(mg), field(mb)]
            }
        }
    }

    /// The layout and header of the result of converting this image.
    pub fn converted(&self, format: Format, op: Option<Op>) -> Output {
        let (bpp, extra): (u16, usize) = match format {
            Format::Gray => (8, 1024),
            Format::Rgb565 => (16, 12),
            Format::Rgb24 => (24, 0),
            Format::Rgb32 => (32, 0),
        };
        let stride = (u64::from(self.width) * u64::from(bpp)).div_ceil(32) * 4;
        let offset = 54 + extra as u64;
        let len = offset + stride * u64::from(self.height);
        let small = |v: u64| if v <= u64::from(u32::MAX) { v as u32 } else { 0 };
        let height = if self.top_down { -(self.height as i32) } else { self.height as i32 };
        let mut h = Vec::new();
        h.extend_from_slice(b"BM");
        for v in [small(len), 0, offset as u32, 40, self.width, height as u32] {
            h.extend_from_slice(&v.to_le_bytes());
        }
        h.extend_from_slice(&1u16.to_le_bytes());
        h.extend_from_slice(&bpp.to_le_bytes());
        let compression = if format == Format::Rgb565 { 3 } else { 0 };
        let colors = if format == Format::Gray { 256 } else { 0 };
        for v in [compression, small(stride * u64::from(self.height)), 2835, 2835, colors, 0] {
            h.extend_from_slice(&v.to_le_bytes());
        }
        match format {
            Format::Rgb565 => [0xF800u32, 0x07E0, 0x001F].iter().for_each(|m| h.extend_from_slice(&m.to_le_bytes())),
            Format::Gray => (0..=255u8).for_each(|i| h.extend_from_slice(&[i, i, i, 0])),
            _ => {}
        }
        assert_eq!(h.len() as u64, offset);
        Output { format, op, header: h, bytes_per_pixel: u64::from(bpp / 8), stride, offset, len }
    }

    /// Fills `buf` with the correct converted file, from byte `pos` on.
    pub fn expected_converted(&self, out: &Output, pos: u64, buf: &mut [u8]) {
        let (w, h) = (u64::from(self.width), u64::from(self.height));
        let horizontal = matches!(out.op, Some(Op::H | Op::Hv));
        let vertical = matches!(out.op, Some(Op::V | Op::Hv));
        let k = out.bytes_per_pixel;
        let to8 = |c: u8, bits: u32| -> u32 { (u32::from(c) * ((1 << bits) - 1) + 127) / 255 };
        for (i, b) in buf.iter_mut().enumerate() {
            let p = pos + i as u64;
            if p < out.offset {
                *b = out.header[p as usize];
                continue;
            }
            let (row, j) = ((p - out.offset) / out.stride, (p - out.offset) % out.stride);
            if j >= w * k {
                *b = 0;
                continue;
            }
            let x = j / k;
            let src_row = if vertical { h - 1 - row } else { row };
            let src_x = if horizontal { w - 1 - x } else { x };
            let [r, g, bl] = self.rgb(src_row, src_x);
            let encoded: [u8; 4] = match out.format {
                Format::Gray => [((77 * u32::from(r) + 150 * u32::from(g) + 29 * u32::from(bl) + 128) >> 8) as u8, 0, 0, 0],
                Format::Rgb565 => {
                    let v = to8(r, 5) << 11 | to8(g, 6) << 5 | to8(bl, 5);
                    [v as u8, (v >> 8) as u8, 0, 0]
                }
                Format::Rgb24 | Format::Rgb32 => [bl, g, r, 0],
            };
            *b = encoded[(j % k) as usize];
        }
    }

    /// Fills `buf` with the correct output of `op`, from file byte `pos` on.
    /// Works out each output pixel's source pixel directly: simple, and
    /// independent of the tricks a fast flip needs.
    pub fn expected(&self, header: &[u8], op: Op, pos: u64, buf: &mut [u8]) {
        let (offset, end, stride, n, mask) = (self.offset(), self.pixel_end(), self.stride(), self.n(), 0xFF << self.shift());
        let (w, h, bpp) = (u64::from(self.width), u64::from(self.height), u64::from(self.bpp));
        let (horizontal, vertical) = (op != Op::V, op != Op::H);
        let mut i = 0;
        while i < buf.len() {
            let p = pos + i as u64;
            if p < offset || p >= end {
                self.fill(header, p, &mut buf[i..i + 1]);
                i += 1;
                continue;
            }
            let (mut row, mut j) = ((p - offset) / stride, (p - offset) % stride);
            let run = ((end - p) as usize).min(buf.len() - i);
            for b in &mut buf[i..i + run] {
                let src = if vertical { h - 1 - row } else { row };
                *b = if j >= n {
                    0
                } else if bpp >= 8 {
                    let k = bpp / 8;
                    let x = if horizontal { w - 1 - j / k } else { j / k };
                    self.byte_in_row(src, x * k + j % k, n, mask)
                } else {
                    let per_byte = 8 / bpp;
                    let mut v = 0u8;
                    for slot in 0..per_byte {
                        let x = j * per_byte + slot;
                        if x < w {
                            let from = if horizontal { w - 1 - x } else { x };
                            v |= (self.pixel(src, from) as u8) << (8 - bpp - slot * bpp);
                        }
                    }
                    v
                };
                j += 1;
                if j == stride {
                    j = 0;
                    row += 1;
                }
            }
            i += run;
        }
    }
}

/// The result of a conversion: its header and layout.
#[derive(Clone, Debug)]
pub struct Output {
    pub format: Format,
    pub op: Option<Op>,
    pub header: Vec<u8>,
    pub bytes_per_pixel: u64,
    pub stride: u64,
    pub offset: u64,
    pub len: u64,
}

// ---------------------------------------------------------------- storage --

pub const CALL_COST: u64 = 65536;

#[derive(Clone, Copy, Debug, Default)]
pub struct Stats {
    pub reads: u64,
    pub writes: u64,
    pub bytes_read: u64,
    pub bytes_written: u64,
}

impl Stats {
    /// The I/O tracker: every call costs as much as moving 64 KiB, as a
    /// request to a disk or a network file server roughly does.
    pub fn cost(&self) -> u64 {
        self.bytes_read + self.bytes_written + CALL_COST * (self.reads + self.writes)
    }
    pub fn add(&self, other: &Stats) -> Stats {
        Stats {
            reads: self.reads + other.reads,
            writes: self.writes + other.writes,
            bytes_read: self.bytes_read + other.bytes_read,
            bytes_written: self.bytes_written + other.bytes_written,
        }
    }
}

/// A read-only input file whose contents are computed on demand.
pub struct Generated {
    pub image: Image,
    header: Vec<u8>,
    pub stats: Stats,
}

impl Generated {
    pub fn new(image: &Image) -> Generated {
        Generated { image: image.clone(), header: image.header(), stats: Stats::default() }
    }
}

impl Storage for Generated {
    fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        harness(|| {
            self.stats.reads += 1;
            let len = self.image.len();
            if offset >= len {
                return Ok(0);
            }
            let k = (buf.len() as u64).min(len - offset) as usize;
            self.image.fill(&self.header, offset, &mut buf[..k]);
            self.stats.bytes_read += k as u64;
            Ok(k)
        })
    }
    fn write_at(&mut self, _: u64, _: &[u8]) -> io::Result<usize> {
        harness(|| Err(io::Error::new(io::ErrorKind::PermissionDenied, "the input file is read-only")))
    }
    fn len(&mut self) -> io::Result<u64> {
        Ok(self.image.len())
    }
}

/// An output file that checks every write against the correct result instead
/// of storing it. Reading from it fails: an output file is for writing.
pub struct Checked {
    image: Image,
    header: Vec<u8>,
    op: Op,
    conversion: Option<Output>,
    /// Written byte ranges, merged: start -> end.
    written: BTreeMap<u64, u64>,
    pub problems: Vec<String>,
    pub stats: Stats,
    scratch: Vec<u8>,
}

impl Checked {
    pub fn new(image: &Image, op: Op) -> Checked {
        Checked { image: image.clone(), header: image.header(), op, conversion: None, written: BTreeMap::new(), problems: Vec::new(), stats: Stats::default(), scratch: Vec::new() }
    }

    /// An output that expects `image` converted to `format`, flipped by `op` if given.
    pub fn converting(image: &Image, format: Format, op: Option<Op>) -> Checked {
        Checked { conversion: Some(image.converted(format, op)), ..Checked::new(image, Op::H) }
    }

    fn expected_len(&self) -> u64 {
        self.conversion.as_ref().map_or(self.image.len(), |c| c.len)
    }

    fn mark(&mut self, start: u64, end: u64) {
        let (mut s, mut e) = (start, end);
        let overlapping: Vec<(u64, u64)> = self.written.range(..=e).rev().take_while(|&(_, &oe)| oe >= s).map(|(&os, &oe)| (os, oe)).collect();
        for (os, oe) in overlapping {
            self.written.remove(&os);
            s = s.min(os);
            e = e.max(oe);
        }
        self.written.insert(s, e);
    }

    /// Ok if every byte of the output was written and every write was correct.
    pub fn verdict(&self) -> Result<(), String> {
        if let Some(p) = self.problems.first() {
            return Err(format!("{p} ({} wrong writes in all)", self.problems.len()));
        }
        let len = self.expected_len();
        match self.written.iter().next() {
            Some((&0, &e)) if e == len && self.written.len() == 1 => Ok(()),
            Some((&0, &e)) => Err(format!("bytes from {e} on were never written (file size {len})")),
            Some((&s, _)) => Err(format!("bytes 0 to {s} were never written")),
            None => Err("nothing was written".into()),
        }
    }

    fn describe(&self, at: u64) -> String {
        if let Some(c) = &self.conversion {
            return if at < c.offset {
                "in the output's headers".into()
            } else {
                let (row, j) = ((at - c.offset) / c.stride, (at - c.offset) % c.stride);
                format!("output row {row}, byte {j} of the row: pixel {} (stride {})", j / c.bytes_per_pixel, c.stride)
            };
        }
        let (offset, end, stride) = (self.image.offset(), self.image.pixel_end(), self.image.stride());
        if at < offset {
            "before the pixel array".into()
        } else if at >= end {
            "after the pixel array".into()
        } else {
            format!("file row {}, byte {} of the row (stride {stride}, {} bytes of pixels)", (at - offset) / stride, (at - offset) % stride, self.image.n())
        }
    }
}

impl Storage for Checked {
    fn read_at(&mut self, _: u64, _: &mut [u8]) -> io::Result<usize> {
        harness(|| Err(io::Error::new(io::ErrorKind::PermissionDenied, "the output file is write-only in this test")))
    }
    fn write_at(&mut self, offset: u64, buf: &[u8]) -> io::Result<usize> {
        harness(|| {
            self.stats.writes += 1;
            self.stats.bytes_written += buf.len() as u64;
            let len = self.expected_len();
            if offset + buf.len() as u64 > len {
                self.problems.push(format!("a write of {} bytes at {offset} goes past the end of the file ({len} bytes)", buf.len()));
                return Ok(buf.len());
            }
            let mut scratch = std::mem::take(&mut self.scratch);
            scratch.resize(buf.len(), 0);
            match &self.conversion {
                Some(c) => self.image.expected_converted(c, offset, &mut scratch),
                None => self.image.expected(&self.header, self.op, offset, &mut scratch),
            }
            if let Some(i) = buf.iter().zip(&scratch).position(|(a, b)| a != b) {
                let at = offset + i as u64;
                if self.problems.len() < 1000 {
                    self.problems.push(format!("byte {at} ({}) written as 0x{:02x}; should be 0x{:02x}", self.describe(at), buf[i], scratch[i]));
                }
            }
            self.scratch = scratch;
            if !buf.is_empty() {
                self.mark(offset, offset + buf.len() as u64);
            }
            Ok(buf.len())
        })
    }
    fn len(&mut self) -> io::Result<u64> {
        Ok(self.written.values().next_back().copied().unwrap_or(0))
    }
}

/// A file in memory, for in-place flips.
pub struct MemFile {
    pub data: Vec<u8>,
    pub stats: Stats,
}

impl MemFile {
    pub fn new(data: Vec<u8>) -> MemFile {
        MemFile { data, stats: Stats::default() }
    }
    /// Compares the file with the correct output of `op` on `image`.
    pub fn verdict(&self, image: &Image, op: Op) -> Result<(), String> {
        if self.data.len() as u64 != image.len() {
            return Err(format!("the file is {} bytes long; it should be {}", self.data.len(), image.len()));
        }
        let header = image.header();
        let mut expected = vec![0; 1 << 16];
        for (k, chunk) in self.data.chunks(1 << 16).enumerate() {
            let pos = (k as u64) << 16;
            let expected = &mut expected[..chunk.len()];
            image.expected(&header, op, pos, expected);
            if let Some(i) = chunk.iter().zip(expected.iter()).position(|(a, b)| a != b) {
                let at = pos + i as u64;
                let place = Checked::new(image, op).describe(at);
                return Err(format!("byte {at} ({place}) is 0x{:02x}; should be 0x{:02x}", chunk[i], expected[i]));
            }
        }
        Ok(())
    }
}

impl Storage for MemFile {
    fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        harness(|| {
            self.stats.reads += 1;
            let len = self.data.len() as u64;
            if offset >= len {
                return Ok(0);
            }
            let k = (buf.len() as u64).min(len - offset) as usize;
            buf[..k].copy_from_slice(&self.data[offset as usize..offset as usize + k]);
            self.stats.bytes_read += k as u64;
            Ok(k)
        })
    }
    fn write_at(&mut self, offset: u64, buf: &[u8]) -> io::Result<usize> {
        harness(|| {
            self.stats.writes += 1;
            self.stats.bytes_written += buf.len() as u64;
            let end = offset as usize + buf.len();
            if end > self.data.len() {
                self.data.resize(end, 0);
            }
            self.data[offset as usize..end].copy_from_slice(buf);
            Ok(buf.len())
        })
    }
    fn len(&mut self) -> io::Result<u64> {
        Ok(self.data.len() as u64)
    }
}

// ---------------------------------------------------------------- misbehaving --

/// xorshift64: deterministic pseudo-random numbers.
pub struct Rng(pub u64);

impl Rng {
    pub fn below(&mut self, n: u64) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0 % n
    }
}

/// Reads and writes fewer bytes than asked, and is sometimes `Interrupted`.
pub struct Flaky<S> {
    pub inner: S,
    rng: Rng,
}

impl<S> Flaky<S> {
    pub fn new(inner: S, seed: u64) -> Flaky<S> {
        Flaky { inner, rng: Rng(seed | 1) }
    }
}

impl<S: Storage> Storage for Flaky<S> {
    fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        if harness(|| self.rng.below(5)) == 0 {
            return Err(io::ErrorKind::Interrupted.into());
        }
        let k = harness(|| if buf.len() > 1 { 1 + self.rng.below(buf.len() as u64) as usize } else { buf.len() });
        self.inner.read_at(offset, &mut buf[..k])
    }
    fn write_at(&mut self, offset: u64, buf: &[u8]) -> io::Result<usize> {
        if harness(|| self.rng.below(5)) == 0 {
            return Err(io::ErrorKind::Interrupted.into());
        }
        let k = harness(|| if buf.len() > 1 { 1 + self.rng.below(buf.len() as u64) as usize } else { buf.len() });
        self.inner.write_at(offset, &buf[..k])
    }
    fn len(&mut self) -> io::Result<u64> {
        self.inner.len()
    }
}

/// Works for `calls` reads and writes, then fails every call.
pub struct FailAfter<S> {
    pub inner: S,
    pub calls: u64,
}

impl<S: Storage> Storage for FailAfter<S> {
    fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        if self.calls == 0 {
            return harness(|| Err(io::Error::other("the disk failed")));
        }
        self.calls -= 1;
        self.inner.read_at(offset, buf)
    }
    fn write_at(&mut self, offset: u64, buf: &[u8]) -> io::Result<usize> {
        if self.calls == 0 {
            return harness(|| Err(io::Error::other("the disk failed")));
        }
        self.calls -= 1;
        self.inner.write_at(offset, buf)
    }
    fn len(&mut self) -> io::Result<u64> {
        self.inner.len()
    }
}

/// Claims to be `extra` bytes longer than its data, as a file truncated by
/// another program after its size was read might.
pub struct LongerThanItIs<S> {
    pub inner: S,
    pub extra: u64,
}

impl<S: Storage> Storage for LongerThanItIs<S> {
    fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        self.inner.read_at(offset, buf)
    }
    fn write_at(&mut self, offset: u64, buf: &[u8]) -> io::Result<usize> {
        self.inner.write_at(offset, buf)
    }
    fn len(&mut self) -> io::Result<u64> {
        Ok(self.inner.len()? + self.extra)
    }
}

// ---------------------------------------------------------------- running --

pub struct Outcome {
    pub result: Result<(), bmpflip::Error>,
    pub peak: usize,
    pub stats: Stats,
    pub verdict: Result<(), String>,
}

/// Flips `image` from a `Generated` input to a `Checked` output.
pub fn flip_generated(image: &Image, op: Op, memory: usize) -> Outcome {
    let (input, output) = (Generated::new(image), Checked::new(image, op));
    let ((result, input, output), peak) = run(move || {
        let (mut input, mut output) = (input, output);
        let result = bmpflip::flip(op, &mut input, &mut output, memory);
        (result, input, output)
    });
    Outcome { result, peak, stats: input.stats.add(&output.stats), verdict: output.verdict() }
}

/// Converts `image` from a `Generated` input to a `Checked` output.
pub fn convert_generated(image: &Image, format: Format, op: Option<Op>, memory: usize) -> Outcome {
    let (input, output) = (Generated::new(image), Checked::converting(image, format, op));
    let ((result, input, output), peak) = run(move || {
        let (mut input, mut output) = (input, output);
        let result = bmpflip::convert(format, op, &mut input, &mut output, memory);
        (result, input, output)
    });
    Outcome { result, peak, stats: input.stats.add(&output.stats), verdict: output.verdict() }
}

/// Flips `image` in place in a `MemFile`.
pub fn flip_in_memory(image: &Image, op: Op, memory: usize) -> Outcome {
    let file = MemFile::new(image.bytes());
    let ((result, file), peak) = run(move || {
        let mut file = file;
        let result = bmpflip::flip_in_place(op, &mut file, memory);
        (result, file)
    });
    let verdict = file.verdict(image, op);
    Outcome { result, peak, stats: file.stats, verdict }
}

/// Checks an outcome: success, correct output, and within the memory budget.
pub fn check(what: &str, outcome: &Outcome, memory: usize) -> Result<(), String> {
    if let Err(e) = &outcome.result {
        return Err(format!("{what}: returned {e:?}"));
    }
    if let Err(e) = &outcome.verdict {
        return Err(format!("{what}: {e}"));
    }
    if outcome.peak > memory {
        return Err(format!("{what}: used {} bytes of heap; the budget is {memory}", outcome.peak));
    }
    Ok(())
}

/// Collects failures and reports the first few.
pub fn all_ok(failures: Vec<String>, total: usize) {
    assert!(failures.is_empty(), "{} of {total} cases fail; the first ones:\n{}", failures.len(), failures.iter().take(6).cloned().collect::<Vec<_>>().join("\n"));
}

pub const OPS: [Op; 3] = [Op::H, Op::V, Op::Hv];
pub const FORMATS: [Format; 4] = [Format::Gray, Format::Rgb565, Format::Rgb24, Format::Rgb32];
