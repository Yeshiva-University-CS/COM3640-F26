#![forbid(unsafe_code)]
//! Flips BMP files of any size, using a fixed amount of memory. See README.md.
//!
//! Keep the names and signatures of everything `pub` below: the tests call them.
//! Everything else (types, helper functions, modules) is yours to design.

pub mod storage;

use std::io;

pub use storage::Storage;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    /// Mirror left to right.
    H,
    /// Mirror top to bottom.
    V,
    /// Both.
    Hv,
}

/// What `convert` writes. See README.md for the exact output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    /// 8 bits per pixel, with a 256-entry gray color table.
    Gray,
    /// 16 bits per pixel: 5 bits red, 6 green, 5 blue (compression 3).
    Rgb565,
    /// 24 bits per pixel: blue, green, red.
    Rgb24,
    /// 32 bits per pixel: blue, green, red, 0.
    Rgb32,
}

/// Why a flip or conversion failed. The program maps these to exit codes 2 to 6.
#[derive(Debug)]
pub enum Error {
    NotBmp,
    Unsupported,
    Truncated,
    /// Reading failed (in place: reading the file).
    Read(io::Error),
    /// Writing failed (in place: writing the file).
    Write(io::Error),
}

/// What the headers say about the pixel array.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Info {
    pub width: u32,
    /// The number of rows, always positive.
    pub height: u32,
    pub bits_per_pixel: u16,
    pub top_down: bool,
    /// Where the pixel array starts.
    pub pixel_offset: u64,
    /// Bytes per row, padding included.
    pub stride: u64,
}

/// The smallest memory budget anyone will pass you.
pub const MIN_MEMORY: usize = 4096;

/// Reads and checks the headers.
pub fn read_info(file: &mut dyn Storage) -> Result<Info, Error> {
    todo!("{:?}", file.len())
}

/// Writes the flipped file to `output`, using at most `memory` bytes of heap.
pub fn flip(op: Op, input: &mut dyn Storage, output: &mut dyn Storage, memory: usize) -> Result<(), Error> {
    todo!("{op:?} {:?} {:?} {memory}", input.len(), output.len())
}

/// Flips `file` where it is, using at most `memory` bytes of heap and no other storage.
pub fn flip_in_place(op: Op, file: &mut dyn Storage, memory: usize) -> Result<(), Error> {
    todo!("{op:?} {:?} {memory}", file.len())
}

/// Writes `input` converted to `format` to `output`, flipped by `op` if given,
/// using at most `memory` bytes of heap.
pub fn convert(format: Format, op: Option<Op>, input: &mut dyn Storage, output: &mut dyn Storage, memory: usize) -> Result<(), Error> {
    todo!("{format:?} {op:?} {:?} {:?} {memory}", input.len(), output.len())
}
