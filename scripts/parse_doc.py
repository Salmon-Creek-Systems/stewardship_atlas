#!/usr/bin/env python3
"""
Minimal Proof of Concept: Document Parser for Geospatial Atlas
Extracts keywords and generates GeoJSON from PDF documents using GPT-4.
Now includes map image analysis using GPT-4V for spatial coverage.
"""

import os
import sys
import json
import argparse
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
import PyPDF2
import openai
from openai import OpenAI
from pdf2image import convert_from_path
import io
from PIL import Image
import h3


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If PDF cannot be read or has no extractable text
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            if len(pdf_reader.pages) == 0:
                raise Exception("PDF has no pages")
            
            text_content = ""
            for page_num, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_content += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
            
            if not text_content.strip():
                raise Exception("No extractable text found in PDF")
                
            return text_content
            
    except Exception as e:
        raise Exception(f"Failed to extract text from PDF: {str(e)}")


def extract_images_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extract images from PDF pages for map analysis.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        List of dictionaries containing page images and metadata
    """
    try:
        # Convert PDF pages to images
        images = convert_from_path(pdf_path, dpi=150)  # Good resolution for analysis
        
        extracted_images = []
        for page_num, image in enumerate(images):
            # Convert PIL image to base64 for API
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='PNG')
            img_str = base64.b64encode(img_buffer.getvalue()).decode()
            
            extracted_images.append({
                'page': page_num + 1,
                'image': img_str,
                'width': image.width,
                'height': image.height
            })
        
        return extracted_images
        
    except Exception as e:
        print(f"Warning: Could not extract images from PDF: {str(e)}")
        return []


def create_gpt_prompt(text_content: str) -> str:
    """
    Create a prompt for GPT-4 to extract keywords and geographic information.
    
    Args:
        text_content: Extracted text from PDF
        
    Returns:
        Formatted prompt string
    """

    return f"""
You are analyzing a document for a geospatial atlas system. Please extract:

1. Up to 10 summary keywords (comma-separated)
2. A classification of the document type as one of "planning document", "academic or professional paper", "grant application", "RFP/Request For Proposals", "Unknown".
2. Geographic references mentioned in the text. Any mention of a place or area should be returned as a bounding box approximating the area mentioned as closely as possible.

Document content:
{text_content[:4000]}  # Limit to first 4000 chars to stay within token limits

Please respond with ONLY a valid JSON object in this exact format:
{{
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "classification": "classification_name",
    "geographic_references": [
    {{
    "name": "Place Name",
    "type": "county|town|region|area",
    "description": "Brief description of what this place is",
    "bounding_box": {{
      "north": latitude,
      "south": latitude,
      "east": longitude,
      "west": longitude
      }},
    "confidence": "high|medium|low"
    }}
    ],
}}

Important: Respond with ONLY the JSON, no other text or explanation.
"""


def create_gpt4v_map_prompt() -> str:
    """
    Create a prompt for GPT-4V to analyze map images for spatial coverage.
    
    Returns:
        Formatted prompt string for image analysis
    """
    return """
Analyze this map image and extract the geographic area it covers.

Focus on:
1. The main geographic region or area shown on the map
2. Approximate bounding box coordinates in WGS84 (latitude/longitude)
3. Key landmarks or features that help define the coverage area

Respond with ONLY a valid JSON object in this exact format:
{
    "map_analysis": {
        "name": "Map Name or Description",
        "description": "What geographic area this map shows",
        "bounding_box": {
            "north": latitude,
            "south": latitude,
            "east": longitude,
            "west": longitude
        },
        "confidence": "high|medium|low",
        "notes": "Any additional observations about the map coverage"
    }
}

Important: 
- don't include the "json` line nore the ``` at the beginning and end - just the JSON itself.
- Respond with ONLY the JSON, no other text
- Use decimal degrees for coordinates
- If you cannot determine coordinates, set confidence to "low"
- Focus on the main area covered, not individual features
"""


def call_gpt4(client: OpenAI, prompt: str) -> Dict[str, Any]:
    """
    Call GPT-4 API to extract information from the document text.
    
    Args:
        client: OpenAI client instance
        prompt: Formatted prompt for GPT-4
        
    Returns:
        Parsed JSON response from GPT-4
        
    Raises:
        Exception: If API call fails or response is invalid
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured information from documents. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,  # Low temperature for consistent, structured output
            max_tokens=1000
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # Try to parse the JSON response
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise Exception(f"GPT-4 returned invalid JSON: {response_text[:200]}... Error: {str(e)}")
            
    except Exception as e:
        raise Exception(f"GPT-4 API call failed: {str(e)}")


def call_gpt4v_for_map(client: OpenAI, image_data: str, prompt: str) -> Dict[str, Any]:
    """
    Call GPT-4V API to analyze map images for spatial coverage.
    
    Args:
        client: OpenAI client instance
        image_data: Base64 encoded image string
        prompt: Formatted prompt for GPT-4V
        
    Returns:
        Parsed JSON response from GPT-4V
        
    Raises:
        Exception: If API call fails or response is invalid
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=500
        )
        
        response_text = response.choices[0].message.content.strip()
        print(f"GPT-4V RESPONSE: {response_text}")
        # Try to parse the JSON response
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise Exception(f"GPT-4V returned invalid JSON: {response_text[:200]}... Error: {str(e)}")
            
    except Exception as e:
        raise Exception(f"GPT-4V API call failed: {str(e)}")


def generate_geojson(gpt_response: Dict[str, Any], map_analysis_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate GeoJSON FeatureCollection from GPT-4 extracted geographic information and map analysis.
    
    Args:
        gpt_response: Parsed response from GPT-4 text analysis
        map_analysis_results: List of parsed responses from GPT-4V map analysis
        
    Returns:
        GeoJSON FeatureCollection as dictionary
    """
    features = []
    
    # Process geographic references from text (Polygon features with bounding boxes)
    print(f"Processing {len(gpt_response.get('geographic_references', []))} text-based geographic references...")
    for ref in gpt_response.get('geographic_references', []):
        feature = make_feature(ref)
        if feature is not None:
            features.append(feature)
            print(f"Generated text feature: {ref.get('name', 'Unknown')} -> H3: {feature['properties'].get('h3_index', 'None')}")
        else:
            print(f"Failed to generate feature for text reference: {ref.get('name', 'Unknown')}")
    
    # Process map analysis results from GPT-4V (Polygon features with actual bounding boxes)
    print(f"Processing {len(map_analysis_results)} image-based map analyses...")
    for map_result in map_analysis_results:
        map_data = map_result.get('map_analysis', {})
        feature = make_feature(map_data)
        if feature is not None:
            features.append(feature)
            print(f"Generated image feature: {map_data.get('name', 'Unknown')} -> H3: {feature['properties'].get('h3_index', 'None')}")
        else:
            print(f"Failed to generate feature for map analysis: {map_data.get('name', 'Unknown')}")
                
    print(f"Successfully generated {len(features)} features")
    
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {
                "name": "EPSG:4326"
            }
        },
        "features": features
    }

def make_feature(map_data):
    bbox = map_data.get('bounding_box', {})
        
    if all(key in bbox for key in ['north', 'south', 'east', 'west']):
        try:
            # Create polygon from bounding box coordinates
            north = float(bbox['north'])
            south = float(bbox['south'])
            east = float(bbox['east'])
            west = float(bbox['west'])
            
            # Validate coordinate ranges
            if not (-90 <= south <= 90) or not (-90 <= north <= 90):
                print(f"Error: Invalid latitude values in bounding box: south={south}, north={north}")
                return None
            if not (-180 <= west <= 180) or not (-180 <= east <= 180):
                print(f"Error: Invalid longitude values in bounding box: west={west}, east={east}")
                return None
            if south >= north:
                print(f"Error: Invalid latitude order: south={south} >= north={north}")
                return None
            if west >= east:
                print(f"Error: Invalid longitude order: west={west} >= east={east}")
                return None
            
            # Create polygon coordinates
            polygon_coords = [
                [west, south],   # Southwest
                [east, south],   # Southeast
                [east, north],   # Northeast
                [west, north],   # Northwest
                [west, south]    # Close the polygon
            ]
            
            # Generate H3 index for the polygon
            # Use resolution 7 for a good balance between precision and index size
            try:
                # Try the newer API first (h3 >= 4.0.0)
                h3_index = h3.polygon_to_cells(
                    {
                        "type": "Polygon",
                        "coordinates": [polygon_coords]
                    },
                    res=7
                )
            except AttributeError:
                # Fallback to older API (h3 < 4.0.0)
                h3_index = h3.polyfill(
                    {
                        "type": "Polygon",
                        "coordinates": [polygon_coords]
                    },
                    res=7
                )
            
            # Convert H3 set to list and take the first index as representative
            h3_list = list(h3_index)
            representative_h3 = h3_list[0] if h3_list else None
            
            return {
                "type": "Feature",
                "properties": {
                    "name": map_data.get('name', 'Unknown'),
                    "description": map_data.get('description', ''),
                    "confidence": map_data.get('confidence', 'unknown'),
                    "notes": map_data.get('notes', ''),
                    "source": "image_analysis",
                    "type": "map_area",
                    "h3_index": representative_h3,
                    "h3_resolution": 7,
                    "h3_coverage_count": len(h3_list),
                    "h3_polyfill": h3_list
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [polygon_coords]
                }
            }
            
        except (ValueError, TypeError) as e:
            print(f"Error: Could not create polygon from bounding box {bbox}: {e}")
            return None
        except Exception as e:
            print(f"Error: Unexpected error creating polygon from bounding box {bbox}: {e}")
            return None
    else:
        missing_keys = [key for key in ['north', 'south', 'east', 'west'] if key not in bbox]
        print(f"Error: Missing required bounding box keys: {missing_keys}")
        print(f"Available keys: {list(bbox.keys())}")
        return None
        


def parse_document(pdf_path: str, api_key: str, skips: list = []) -> Dict[str, Any]:
    """
    Main function to parse a PDF document and extract structured information.
    
    Args:
        pdf_path: Path to the PDF file
        api_key: OpenAI API key
        
    Returns:
        Dictionary containing keywords and GeoJSON
    """
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=api_key)
        
        # Extract text from PDF
        print(f"Extracting text from: {pdf_path}")
        text_content = extract_text_from_pdf(pdf_path)
        print(f"Extracted {len(text_content)} characters of text")
            
        # Create GPT-4 prompt for text analysis
        prompt = create_gpt_prompt(text_content)
        
        # Call GPT-4 for text analysis
        print("Calling GPT-4 for text analysis...")
        gpt_response = call_gpt4(client, prompt)
        print(f"GPT-4 text analysis complete:\n{gpt_response}")
        
        extracted_images = []
        map_analysis_results = []
        if 'image' not in skips:
            # Extract images from PDF for map analysis
            print("Extracting images from PDF...")
            extracted_images = extract_images_from_pdf(pdf_path)
            print(f"Extracted {len(extracted_images)} page images")


            # Analyze images with GPT-4V for map coverage

            if extracted_images:
                print("Analyzing images with GPT-4V for map coverage...")
                map_prompt = create_gpt4v_map_prompt()
            
                for img_data in extracted_images:
                    try:
                        print(f"Analyzing page {img_data['page']}...")
                        map_result = call_gpt4v_for_map(client, img_data['image'], map_prompt)
                        map_analysis_results.append(map_result)
                        print(f"Page {img_data['page']} analysis complete")
                    except Exception as e:
                        print(f"Warning: Failed to analyze page {img_data['page']}: {e}")
                        continue
        else:
            print("Skipping image analysis.")
            
        # Generate GeoJSON combining text and image analysis
        geojson = generate_geojson(gpt_response, map_analysis_results)
        
        # Prepare final output
        result = {
            "keywords": gpt_response.get('keywords', []),
            "text_response" : gpt_response,
            "geojson": geojson if geojson is not None else None,
            "metadata": {
                "source_file": pdf_path,
                "extraction_method": "gpt4_text_and_vision_analysis",
                "text_length": len(text_content),
                "images_analyzed": len(extracted_images),
                "features_extracted": len(geojson['features']),
                "map_analysis_pages": [img['page'] for img in extracted_images]
            }
        }
        
        return result
        
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "source_file": pdf_path
        }


def main():
    """Main entry point for command line usage."""
    parser = argparse.ArgumentParser(description='Parse PDF documents for geospatial atlas')
    parser.add_argument('pdf_path', help='Path to the PDF file to parse')
    parser.add_argument('--api-key', help='OpenAI API key (or set OPENAI_API_KEY env var)')
    parser.add_argument('--skips', '-s', help='steps to skip')
    
    args = parser.parse_args()

    skips = [s.strip() for s in args.skips.split(',')] if args.skips else []
    print(f"Skips: {skips}")
    
    # Get API key
    api_key = args.api_key or os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("Error: OpenAI API key required. Set OPENAI_API_KEY environment variable or use --api-key")
        sys.exit(1)
    
    # Check if PDF exists
    if not os.path.exists(args.pdf_path):
        print(f"Error: PDF file not found: {args.pdf_path}")
        sys.exit(1)
    p = Path(args.pdf_path)
    # dir = p.parent
    
    # Parse document
    result = parse_document(args.pdf_path, api_key, skips=skips)
    
    # Output results
    #if args.output:
    geojson_path = p.parent / f"{p.stem}.geojson"
    if result.get('geojson'):
        with open(geojson_path, 'w') as f:
            json.dump(result['geojson'], f, indent=2)
        print(f"GJSON Results saved to: {geojson_path}")

        result['geojson'] = str(geojson_path)

    with open(p.parent / f"{p.stem}.json", 'w') as f:
        json.dump(result, f, indent=2)
    print(f"JSON Results saved to JSON")

    
if __name__ == "__main__":
    main() 
