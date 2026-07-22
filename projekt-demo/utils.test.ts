import { describe, it, expect } from 'vitest'

describe('Math utilities', () => {
  it('should add two numbers correctly', () => {
    expect(2 + 2).toBe(4)
    expect(5 + 3).toBe(8)
  })

  it('should subtract two numbers correctly', () => {
    expect(10 - 3).toBe(7)
    expect(5 - 5).toBe(0)
  })

  it('should handle edge cases', () => {
    expect(0 + 0).toBe(0)
    expect(-1 + 1).toBe(0)
  })
})

describe('String utilities', () => {
  it('should concatenate strings', () => {
    expect('hello' + ' ' + 'world').toBe('hello world')
  })

  it('should handle empty strings', () => {
    expect(''.length).toBe(0)
    expect('test'.length).toBe(4)
  })
})